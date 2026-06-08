import os
import time
import string
import random
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, redirect, Response
from flask_cors import CORS
from dotenv import load_dotenv

# استيراد الأدوات من ملف db_manager الخاص بك
from db_manager import execute_query, supabase

load_dotenv()

app = Flask(__name__)

# إعداد CORS بشكل صحيح لمنع تكرار الـ Headers
CORS(app, resources={r"/api/*": {"origins": "*"}})

# --- دالة توليد كود الحجز ---
def generate_booking_code(length=5):
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choices(characters, k=length))

def promote_waiting_list(inventory_id):
    """تحويل المرضى من قائمة الانتظار إلى مؤكد إذا توفر مخزن"""
    try:
        # 1. جلب المتاح حالياً من الـ View (اللي بيخصم الـ confirmed والـ pending)
        inv_res = supabase.table('vw_smart_inventory_search').select('available_stock').eq('inventory_id', inventory_id).single().execute()
        available = inv_res.data['available_stock'] if inv_res.data else 0

        # 2. طالما فيه متاح، حاول ترقي الناس بالترتيب (الأقدم أولاً)
        while available > 0:
            waiting_res = supabase.table('bookings') \
                .select('*') \
                .eq('inventory_id', inventory_id) \
                .eq('status', 'waiting') \
                .order('created_at', desc=False) \
                .limit(1).execute()

            if not waiting_res.data:
                break # مفيش حد مستني تاني

            candidate = waiting_res.data[0]
            if available >= candidate['reserved_quantity']:
                # ترقية المريض
                expiry = (datetime.now() + timedelta(hours=24)).isoformat()
                supabase.table('bookings').update({
                    "status": "confirmed",
                    "expires_at": expiry
                }).eq('booking_id', candidate['booking_id']).execute()

                # تحديث المتاح للحلقة القادمة (Loop)
                available -= candidate['reserved_quantity']
            else:
                break # المتاح لا يكفي الشخص التالي في الطابور
    except Exception as e:
        print(f"Promotion Error: {str(e)}")


@app.route('/test')
def test():
    return {"message": "Dawa-Go Backend is Online! 🚀"}

# ==========================================
# 1. نظام المصادقة (Authentication)
# ==========================================

@app.route('/api/auth/signup', methods=['POST'])
def signup():
    try:
        data = request.json
        role = data.get('role')
        phone = data.get('phone')
        email = data.get('email')

        if phone:
            existing_phone = supabase.table('users_profile').select('phone').eq('phone', phone).execute()
            if existing_phone.data:
                return jsonify({"status": "error", "message": "رقم الهاتف مسجل بالفعل"}), 400

        existing_email = supabase.table('users_profile').select('email').eq('email', email).execute()
        if existing_email.data:
            return jsonify({"status": "error", "message": "البريد الإلكتروني مسجل بالفعل"}), 400

        user_metadata = {
            "full_name": data.get('full_name'),
            "role": role,
            "phone": phone
        }

        if role == 'pharmacy':
            user_metadata.update({
                "pharmacy_name": data.get('pharmacy_name'),
                "governorate": data.get('governorate'),
                "city_center": data.get('city_center'),
                "district_village": data.get('district_village'),
                "google_maps_link": data.get('gmap_url'),
                "latitude": data.get('latitude'),
                "longitude": data.get('longitude')
            })
        else:
            user_metadata.update({
                "latitude": data.get('latitude'),
                "longitude": data.get('longitude')
            })

        auth_res = supabase.auth.sign_up({
            "email": email,
            "password": data.get('password'),
            "options": {
                "data": user_metadata,
                "email_redirect_to": "https://mahmoud2albehwar.pythonanywhere.com/verify"
            }
        })
        return jsonify({"status": "success", "message": "تم إرسال بريد التفعيل"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/verify')
def verify_email():
    token_hash = request.args.get('token_hash')
    auth_type = request.args.get('type', 'signup')
    signin_page = "http://127.0.0.1:5500/frontend/myfrontend/pages/signin.html"
    if not token_hash: return redirect(f"{signin_page}?error=missing_token")
    try:
        supabase.auth.verify_otp({"token_hash": token_hash, "type": auth_type})
        return redirect(f"{signin_page}?verified=true")
    except Exception as e:
        return redirect(f"{signin_page}?error=verification_failed")

@app.route('/api/auth/signin', methods=['POST'])
def signin():
    data = request.json
    try:
        response = supabase.auth.sign_in_with_password({"email": data.get('email'), "password": data.get('password')})
        if response.session:
            return jsonify({
                "message": "تم تسجيل الدخول بنجاح",
                "user": response.user.id,
                "session": response.session.access_token
            }), 200
        return jsonify({"message": "بيانات الدخول غير صحيحة"}), 401
    except Exception as e:
        return jsonify({"message": "خطأ في الإيميل أو كلمة المرور"}), 401

# ==========================================
# 2. إدارة الملف الشخصي (Profile)
# ==========================================

@app.route('/api/user/profile/<user_id>', methods=['GET'])
def get_user_profile(user_id):
    try:
        user_query = supabase.table('users_profile').select('*').eq('user_id', user_id).single().execute()
        if not user_query.data: return jsonify({"status": "error", "message": "المستخدم غير موجود"}), 404

        user_data = user_query.data
        role = user_data.get('role')

        if role == 'pharmacy':
            details = supabase.table('pharmacies_details').select('*').eq('pharmacy_id', user_id).single().execute()
            user_data['details'] = details.data if details.data else {}
            bookings = supabase.table('bookings').select('status').eq('pharmacy_id', user_id).execute()
        else:
            details = supabase.table('customers_details').select('*').eq('customer_id', user_id).single().execute()
            user_data['details'] = details.data if details.data else {}
            bookings = supabase.table('bookings').select('status').eq('customer_id', user_id).execute()

        user_data['bookings'] = bookings.data if bookings.data else []
        return jsonify({"status": "success", "data": user_data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/user/profile/update/<user_id>', methods=['PUT'])
def update_user_profile(user_id):
    try:
        data = request.json
        role = data.get('role')
        supabase.table('users_profile').update({"full_name": data.get('full_name'), "phone": data.get('phone')}).eq('user_id', user_id).execute()

        if role == 'pharmacy':
            pharmacy_data = {
                "pharmacy_name": data.get('pharmacy_name'), "governorate": data.get('governorate'),
                "city_center": data.get('city_center'), "district_village": data.get('district_village'),
                "google_maps_link": data.get('google_maps_link'), "address_description": data.get('address_description'),
                "latitude": float(data.get('latitude')) if data.get('latitude') else None,
                "longitude": float(data.get('longitude')) if data.get('longitude') else None,
                "pharmacy_id": user_id
            }
            supabase.table('pharmacies_details').upsert(pharmacy_data).execute()
        return jsonify({"status": "success", "message": "تم تحديث البيانات"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

# ==========================================
# 3. إدارة المخزن (Inventory)
# ==========================================

@app.route('/api/pharmacy/medicines-list', methods=['GET'])
def get_medicines_list():
    try:
        response = supabase.table('medications').select('med_name').execute()
        names = [item['med_name'] for item in response.data]
        return jsonify({"status": "success", "data": names}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/pharmacy/get-inventory', methods=['GET'])
def get_inventory():
    try:
        user_id = request.args.get('userId')
        res = supabase.table('vw_pharmacist_inventory').select('*').eq('pharmacy_id', user_id).execute()
        return jsonify({"status": "success", "data": res.data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/pharmacy/add-item', methods=['POST'])
def add_inventory_item():
    try:
        data = request.json
        user_id, med_name, description, image_url , expiry = data.get('userId'), data.get('name'), data.get('description'), data.get('imageUrl'), data.get('expiry')

        # جلب أو إنشاء الدواء
        med_res = supabase.table('medications').select('med_id').eq('med_name', med_name).execute()
        if not med_res.data:
            new_med = supabase.table('medications').insert({"med_name": med_name,"description":description,"image_url":image_url , "units_per_package": data.get('units_per_package', 1)}).execute()
            med_id = new_med.data[0]['med_id']
        else:
            med_id = med_res.data[0]['med_id']

        # فحص التكرار
        exist = supabase.table('inventory').select('*').eq('pharmacy_id', user_id).eq('med_id', med_id).eq('expiry_date', expiry).execute()
        if exist.data:
            return jsonify({"status": "exists", "message": "الصنف موجود مسبقاً"}), 409

        # الحساب والحفظ
        total = (int(data.get('pkgs', 0)) * int(data.get('units_per_package', 1))) + int(data.get('extra_units', 0))
        insert_res = supabase.table('inventory').insert({
            "pharmacy_id": user_id, "med_id": med_id, "expiry_date": expiry, "total_units": total, "price": data.get('price')
        }).execute()

        # --- التعديل الجديد ---
        if insert_res.data:
            promote_waiting_list(insert_res.data[0]['inventory_id'])

        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/api/pharmacy/update-inventory', methods=['PUT'])
def update_inventory():
    try:
        data = request.json
        inv_id = data.get('inventoryId')

        # 1. جلب البيانات الحالية والمحجوز "المؤكد" فقط
        # سنستخدم الفيو 'vw_smart_inventory_search' لجلب الـ total_reserved
        # أو نحسبها مباشرة من جدول الحجوزات لضمان الدقة اللحظية
        reserved_res = supabase.table('bookings') \
            .select('reserved_quantity') \
            .eq('inventory_id', inv_id) \
            .eq('status', 'confirmed') \
            .execute()

        total_confirmed = sum(item['reserved_quantity'] for item in reserved_res.data)

        # 2. جلب معامل التحويل (كم وحدة في العلبة)
        res = supabase.table('inventory').select('med_id').eq('inventory_id', inv_id).single().execute()
        med_res = supabase.table('medications').select('units_per_package').eq('med_id', res.data['med_id']).single().execute()
        units_per_pkg = med_res.data['units_per_package']

        # 3. حساب الكمية الجديدة التي يرغب الصيدلي في إدخالها
        pkgs = data.get('pkgs')
        units = data.get('units')

        if pkgs is not None and units is not None:
            new_total = (int(pkgs) * units_per_pkg) + int(units)
        else:
            # لو لم يغير الكمية (تغيير سعر فقط مثلاً)
            inv_current = supabase.table('inventory').select('total_units').eq('inventory_id', inv_id).single().execute()
            new_total = inv_current.data['total_units']

        # --- الحماية الذكية ---
        # 4. منع الصيدلي من خفض المخزن لأقل من المحجوز مؤكداً
        if new_total < total_confirmed:
            return jsonify({
                "status": "error",
                "message": f"لا يمكن خفض المخزن لأقل من {total_confirmed} وحدة (الكمية المحجوزة حالياً لمؤكدين)."
            }), 400

        # 5. تنفيذ التحديث إذا اجتاز الفحص
        update_fields = {"total_units": new_total}
        if data.get('price'):
            update_fields["price"] = float(data.get('price'))

        supabase.table('inventory').update(update_fields).eq('inventory_id', inv_id).execute()

        # 6. تشغيل محرك الترقية (إذا زاد المخزن)
        promote_waiting_list(inv_id)

        return jsonify({"status": "success", "message": "تم تحديث المخزن بنجاح."}), 200

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

# ==========================================
# 4. محرك البحث والحجوزات (Booking System)
# ==========================================

@app.route('/api/public/search-medications', methods=['GET'])
def search_medications_public():
    try:
        query = request.args.get('query', '').strip()
        if not query: return jsonify({"status": "error", "message": "أدخل اسم الدواء"}), 400
        res = supabase.table('vw_smart_inventory_search').select('*').ilike('med_name', f'%{query}%').order('available_stock', desc=True).execute()
        return jsonify({"status": "success", "count": len(res.data), "data": res.data}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/public/create-booking', methods=['POST'])
def create_booking():
    try:
        data = request.get_json()
        customer_id = data.get('customer_id')
        inventory_id = data.get('inventory_id')
        pharmacy_id = data.get('pharmacy_id')

        # 1. منع التكرار: التأكد من عدم وجود حجز نشط لنفس الدواء والعميل
        existing = supabase.table('bookings').select('booking_id') \
            .eq('customer_id', customer_id) \
            .eq('inventory_id', inventory_id) \
            .in_('status', ['pending', 'confirmed', 'waiting']) \
            .execute()

        if existing.data:
            return jsonify({"status": "error", "message": "لديك حجز نشط بالفعل لهذا الدواء في هذه الصيدلية"}), 400

        # 2. جلب بيانات الدواء (السعر والمعيار)
        inv_res = supabase.table('vw_smart_inventory_search').select('*').eq('inventory_id', inventory_id).execute()
        if not inv_res.data:
            return jsonify({"status": "error", "message": "الدواء غير متوفر حالياً"}), 404
        inv_data = inv_res.data[0]
        qty = int(data.get('quantity', 1))
        # حساب إجمالي الوحدات المطلوبة (أشرطة)
        final_units = (qty * inv_data['units_per_package']) if data.get('unit_type') == 'package' else qty

        # 3. المنطق الموحد: أي طلب جديد حالته الافتراضية "pending" ليعرض على الصيدلي
        status = "pending"
        # مهلة أولية للمراجعة (مثلاً 12 ساعة ليقوم الصيدلي بالرد)
        expiration = (datetime.now() + timedelta(hours=12)).isoformat()

        res = supabase.table('bookings').insert({
            "customer_id": customer_id,
            "pharmacy_id": pharmacy_id,
            "inventory_id": inventory_id,
            "booking_code": f"DG-{generate_booking_code()}",
            "status": status,
            "reserved_quantity": final_units,
            "reserved_price": inv_data['unit_price'],
            "expires_at": expiration,
            "created_at": datetime.now().isoformat()
        }).execute()

        return jsonify({
            "status": "success",
            "message": "تم إرسال طلب الحجز للصيدلية بنجاح، يرجى انتظار التأكيد",
            "data": res.data[0]
        }), 201

    except Exception as e:
        print(f"Booking Error: {str(e)}")
        return jsonify({"status": "error", "message": "حدث خطأ أثناء إرسال الطلب"}), 500

# ==========================================
# 5. التحكم في الطلبات (Pharmacist Dashboard)
# ==========================================

@app.route('/api/pharmacy/get-bookings', methods=['GET'])
def get_pharmacy_bookings():
    try:
        pharmacy_id = request.args.get('pharmacy_id')
        if not pharmacy_id:
            return jsonify({"status": "error", "message": "pharmacy_id is required"}), 400

        # 1. جلب البيانات من الـ Views
        # الطلبات العادية
        orders = supabase.table('vw_pharmacist_orders').select('*').eq('pharmacy_id', pharmacy_id).execute()

        # قائمة الانتظار (تأكد أن الـ View دي فيها pharmacy_id مباشرة لتجنب أخطاء الربط)
        waiting = supabase.table('vw_waiting_list_ranks').select('*').eq('pharmacy_id', pharmacy_id).execute()

        # 2. منطق الدمج الاحترافي باستخدام Dictionary
        merged_data = {}

        # إضافة الطلبات العادية أولاً
        for item in (orders.data or []):
            b_id = item['booking_id']
            merged_data[b_id] = item

        # دمج بيانات الانتظار (الـ Rank) مع الحجز الأصلي
        for item in (waiting.data or []):
            b_id = item['booking_id']
            if b_id in merged_data:
                # إذا كان موجوداً، أضف له الـ queue_rank فقط
                merged_data[b_id]['queue_rank'] = item.get('queue_rank')
            else:
                # إذا لم يكن موجوداً (حالة احتياطية)، أضفه كاملاً
                merged_data[b_id] = item

        # تحويل القاموس مرة أخرى إلى قائمة (List) ليرسل كـ JSON
        final_list = list(merged_data.values())

        return jsonify({"status": "success", "data": final_list}), 200

    except Exception as e:
        print(f"Error in get_bookings: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/pharmacy/respond-booking', methods=['POST'])
def respond_booking():
    try:
        data = request.json
        booking_id = data.get('booking_id')
        action = data.get('action')

        if action == 'reject':
            supabase.table('bookings').update({
                "status": "expired",
                "expires_at": None
            }).eq('booking_id', booking_id).execute()
            return jsonify({"status": "success", "message": "تم رفض الطلب بنجاح", "final_status": "expired"}), 200

        if action == 'accept':
            # 1. جلب بيانات الحجز الحالي (عشان نعرف الكمية المطلوبة)
            booking_res = supabase.table('bookings').select('*').eq('booking_id', booking_id).single().execute()
            if not booking_res.data:
                return jsonify({"status": "error", "message": "الحجز غير موجود"}), 404

            booking = booking_res.data
            requested_qty = booking['reserved_quantity']
            inventory_id = booking['inventory_id']

            # 2. جلب المتاح من الفيو (دلوقتي الفيو بتخصم الـ confirmed فقط)
            inv_res = supabase.table('vw_smart_inventory_search').select('available_stock').eq('inventory_id', inventory_id).single().execute()

            # المتاح الفعلي اللي لسه متبعش ولا اتأكد
            available_stock = inv_res.data['available_stock'] if inv_res.data else 0

            # 3. القرار النهائي (مقارنة مباشرة)
            # بما أن المريض الحالي حالته 'pending'، فالفيو لسه مخصمتوش.
            # إذن نقارن المتاح مباشرة بالكمية المطلوبة.
            if available_stock >= requested_qty:
                new_status = 'confirmed'
                # صلاحية الاستلام 24 ساعة من لحظة التأكيد
                expiry = (datetime.now() + timedelta(hours=24)).isoformat()
                msg = "تم تأكيد الحجز بنجاح."
            else:
                # لو المتاح أقل من المطلوب (بسبب حجوزات أكدتها لمرضى تانيين قبله)
                new_status = 'waiting'
                expiry = None
                msg = "المخزن غير كافٍ حالياً، تم النقل لقائمة الانتظار."

            # 4. التحديث النهائي في قاعدة البيانات
            supabase.table('bookings').update({
                "status": new_status,
                "expires_at": expiry
            }).eq('booking_id', booking_id).execute()

            return jsonify({
                "status": "success",
                "message": msg,
                "final_status": new_status
            }), 200

    except Exception as e:
        print(f"Error in respond_booking: {str(e)}")
        return jsonify({"status": "error", "message": "حدث خطأ أثناء معالجة الطلب"}), 500

@app.route('/api/pharmacy/complete-sale', methods=['POST'])
def complete_booking():
    try:
        data = request.json
        booking_id = data.get('booking_id')

        if not booking_id:
            return jsonify({"status": "error", "message": "booking_id is required"}), 400

        # 1. جلب بيانات الحجز (بدون .single() لتجنب خطأ الـ 0 إذا لم يوجد سجل)
        booking_res = supabase.table('bookings').select('*').eq('booking_id', booking_id).execute()

        if not booking_res.data:
            return jsonify({"status": "error", "message": "الحجز غير موجود"}), 404

        booking = booking_res.data[0]
        inventory_id = booking['inventory_id']
        qty_delivered = booking['reserved_quantity']

        # 2. تحديث الحجز الحالي لمكتمل
        supabase.table('bookings').update({"status": "completed"}).eq('booking_id', booking_id).execute()

        # 3. خصم المخزن الفعلي (Physical)
        # جلب الكمية الحالية
        inv_data = supabase.table('inventory').select('total_units').eq('inventory_id', inventory_id).execute()

        if inv_data.data:
            current_physical = inv_data.data[0]['total_units']
            new_total = max(0, current_physical - qty_delivered)

            # تحديث الجدول الحقيقي
            supabase.table('inventory').update({"total_units": new_total}).eq('inventory_id', inventory_id).execute()

            # 4. المنطق اللي اتفقنا عليه:
            # لو المخزن الفيزيائي خلص تماماً، نلغي قائمة الانتظار
            if new_total <= 0:
                supabase.table('bookings').update({"status": "cancelled"})\
                    .eq('inventory_id', inventory_id)\
                    .eq('status', 'waiting').execute()
                msg = "تم التسليم بنجاح، ونفدت الكمية (تم إلغاء الانتظار)."
            else:
                msg = "تم التسليم بنجاح وتحديث المخزن."
        else:
            msg = "تم التسليم، ولكن لم يتم العثور على الصنف في المخزن لتحديث الكمية."

        return jsonify({"status": "success", "message": msg}), 200

    except Exception as e:
        # هنا الخطأ 0 بيتحول لرسالة مفهومة
        print(f"Detailed Error: {str(e)}")
        return jsonify({"status": "error", "message": "حدث خطأ داخلي في السيرفر"}), 500

@app.route('/api/pharmacy/sales-history', methods=['GET'])
def get_sales_history():
    try:
        pharmacy_id = request.args.get('pharmacy_id')
        if not pharmacy_id:
            return jsonify({"status": "error", "message": "pharmacy_id مطلوب"}), 400

        # جلب البيانات من الفيو التي قمت بإنشائها
        res = supabase.table('vw_pharmacist_orders') \
            .select('*') \
            .eq('pharmacy_id', pharmacy_id) \
            .eq('status', 'completed') \
            .order('created_at', desc=True) \
            .execute()

        # البيانات ستخرج بنفس أسماء الأعمدة في الفيو
        return jsonify({
            "status": "success",
            "data": res.data
        }), 200

    except Exception as e:
        print(f"Sales History Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/public/cancel-booking', methods=['POST'])
def cancel_booking():
    try:
        data = request.json
        booking_id = data.get('booking_id')

        if not booking_id:
            return jsonify({"status": "error", "message": "booking_id مطلوب"}), 400

        # 1. جلب inventory_id أولاً قبل التحديث (لتجنب مشاكل الـ Chain)
        booking_info = supabase.table('bookings') \
            .select('inventory_id') \
            .eq('booking_id', booking_id) \
            .execute()

        if not booking_info.data:
            return jsonify({"status": "error", "message": "الحجز غير موجود"}), 404

        inventory_id = booking_info.data[0]['inventory_id']

        # 2. تحديث حالة الحجز إلى ملغي
        supabase.table('bookings') \
            .update({"status": "cancelled", "expires_at": None}) \
            .eq('booking_id', booking_id) \
            .execute()

        # 3. تشغيل محرك الترقية لخدمة الشخص التالي في الانتظار
        promote_waiting_list(inventory_id)

        return jsonify({
            "status": "success",
            "message": "تم إلغاء الحجز وتحديث قائمة الانتظار تلقائياً"
        }), 200

    except Exception as e:
        print(f"Cancel Booking Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/public/my-bookings', methods=['GET'])
def get_my_bookings():
    """
    رووت جلب حجوزات المريض (الحالية، الانتظار، والسابقة)
    من الفيو المحدثة vw_patient_bookings_display
    """
    try:
        # جلب الـ user_id من الـ Query Parameters
        user_id = request.args.get('user_id')

        if not user_id:
            return jsonify({
                "status": "error",
                "message": "user_id مطلوب للوصول للحجوزات"
            }), 400

        # الاستعلام من الفيو المحدثة
        # الفيو دي بتعمل Join تلقائي مع:
        # 1. medications (لجلب اسم الدواء وصورته)
        # 2. users_profile (لجلب اسم الصيدلية ورقم تليفونها)
        # 3. pharmacies_details (لجلب اللوكيشن والمنطقة والتقييم)
        res = supabase.table('vw_patient_bookings_display') \
            .select('*') \
            .eq('user_id', user_id) \
            .order('created_at', desc=True) \
            .execute()

        # إرجاع البيانات للفرونت إند ليتم توزيعها على التبويبات
        return jsonify({
            "status": "success",
            "count": len(res.data),
            "data": res.data
        }), 200

    except Exception as e:
        print(f"Error fetching patient bookings: {str(e)}")
        return jsonify({
            "status": "error",
            "message": "حدث خطأ أثناء جلب البيانات من السيرفر"
        }), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
