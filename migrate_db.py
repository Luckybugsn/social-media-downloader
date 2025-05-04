from app import app, db
from sqlalchemy import text

with app.app_context():
    # Add the platform column to the download table if it doesn't exist
    try:
        db.session.execute(text("ALTER TABLE download ADD COLUMN IF NOT EXISTS platform VARCHAR(20) DEFAULT 'youtube'"))
        db.session.commit()
        print("Migration 1 successful: Added 'platform' column to download table")
    except Exception as e:
        db.session.rollback()
        print(f"Error during migration 1: {e}")
        
    # Increase the size of the thumbnail_url column to handle longer URLs
    try:
        db.session.execute(text("ALTER TABLE download ALTER COLUMN thumbnail_url TYPE VARCHAR(1024)"))
        db.session.commit()
        print("Migration 2 successful: Increased size of 'thumbnail_url' column to 1024 characters")
    except Exception as e:
        db.session.rollback()
        print(f"Error during migration 2: {e}")