import psycopg2

try:
    conn = psycopg2.connect(
        host="localhost",
        port=5432,
        dbname="Connect4DB",
        user="postgres",
        password="123"
    )
    

    print("✅ Connexion PostgreSQL OK")
    conn.close()
except Exception as e:
    print("❌ Erreur :", repr(e))
