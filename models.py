from datetime import datetime
from app import db

class Download(db.Model):
    """Model for tracking video downloads."""
    id = db.Column(db.Integer, primary_key=True)
    video_id = db.Column(db.String(50), nullable=False)  # Increased size for other platforms' IDs
    title = db.Column(db.String(255), nullable=False)
    format = db.Column(db.String(50), nullable=False)
    platform = db.Column(db.String(20), nullable=True, default='youtube')  # Store platform info
    download_date = db.Column(db.DateTime, default=datetime.utcnow)
    filesize = db.Column(db.BigInteger, nullable=True)
    thumbnail_url = db.Column(db.String(1024), nullable=True)  # Increased size for longer Instagram URLs
    duration = db.Column(db.Integer, nullable=True)
    
    def __repr__(self):
        return f'<Download {self.title}>'