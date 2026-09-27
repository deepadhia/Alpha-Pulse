"""
test_db_connection.py
Simple script to verify MongoDB connectivity and write permissions.
"""
import os
import sys
from datetime import datetime, timezone
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

def test_connection():
    uri = os.getenv("MONGO_URI")
    if not uri:
        print("ERROR: MONGO_URI not found in .env file.")
        return

    print(f"Attempting to connect to MongoDB...")
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        # Force a connection check
        client.admin.command('ping')
        print("Connection Successful!")
        
        db = client["ipo_scanner_v2"]
        print(f"Database: {db.name}")
        
        # Test write permission in a temporary collection
        test_col = db["_connection_test"]
        test_doc = {
            "test": True,
            "timestamp": datetime.now(timezone.utc),
            "pid": os.getpid()
        }
        
        print("Testing write permission...")
        res = test_col.insert_one(test_doc)
        if res.inserted_id:
            print(f"Write successful! (ID: {res.inserted_id})")
            
            # Clean up
            test_col.delete_one({"_id": res.inserted_id})
            print("Cleanup successful.")
            
        print("\nDB Infrastructure is READY.")
        
    except Exception as e:
        print(f"ERROR: Connection or write test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    test_connection()
