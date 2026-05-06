import os
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# تهيئة المتغيرات وتصديرها
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def execute_query(action, table, data=None, filters=None, ilike_filter=None):
    try:
        if action == 'insert':
            response = supabase.table(table).insert(data).execute()
            return response.data

        elif action == 'select':
            query = supabase.table(table).select("*")

            # الفلاتر العادية (التطابق التام)
            if filters:
                for key, value in filters.items():
                    query = query.eq(key, value)

            # الفلتر الصايع للبحث الجزئي (زي SQL LIKE)
            if ilike_filter: # ilike_filter={'column': 'name', 'value': 'panadol'}
                query = query.ilike(ilike_filter['column'], f"%{ilike_filter['value']}%")

            response = query.execute()
            return response.data
    except Exception as e:
        print(f"Error: {e}")
        raise e