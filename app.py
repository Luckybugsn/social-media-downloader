import os
import logging
import tempfile
import re
import uuid
from datetime import datetime
from urllib.parse import urlparse
from flask import Flask, render_template, request, jsonify, send_file, session, abort, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
import yt_dlp

# Set up logging
logging.basicConfig(level=logging.DEBUG)

# Initialize database
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "youtube-dl-secret-key")

# Configure database
database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Required for PostgreSQL compatibility with SQLAlchemy
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///youtube_downloader.db"

app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
}
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

# Import models and create tables
with app.app_context():
    from models import Download
    db.create_all()
    
# Template filters
@app.template_filter('format_duration')
def format_duration(seconds):
    """Format duration in seconds to HH:MM:SS format"""
    if not seconds:
        return "Unknown"
    
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        return f"{minutes}:{seconds:02d}"
        
@app.template_filter('format_filesize')
def format_filesize(bytes):
    """Format bytes to human-readable file size"""
    if not bytes:
        return "Unknown"
    
    # Define unit suffixes
    suffixes = ['B', 'KB', 'MB', 'GB', 'TB']
    
    # Determine the appropriate suffix
    i = 0
    while bytes >= 1024 and i < len(suffixes) - 1:
        bytes /= 1024
        i += 1
    
    # Format with 2 decimal places if not bytes
    if i == 0:
        return f"{bytes} {suffixes[i]}"
    else:
        return f"{bytes:.2f} {suffixes[i]}"

# Create a temporary directory to store downloaded videos
temp_dir = tempfile.mkdtemp()
logging.debug(f"Created temporary directory at {temp_dir}")

# Dictionary to store download info by session
downloads = {}

def validate_url(url):
    """Validate that the URL is from a supported platform."""
    parsed_url = urlparse(url)
    hostname = parsed_url.netloc.lower()
    
    # Define patterns for different platforms
    patterns = {
        # YouTube
        'youtube': {
            'domains': ['www.youtube.com', 'youtube.com', 'youtu.be', 'm.youtube.com'],
            'patterns': [
                r'(youtu\.be\/|youtube\.com\/(watch\?(.*&)?v=|(embed|v)\/))([^?&"\'>]+)',  # Standard
                r'youtube\.com\/shorts\/([^?&"\'>]+)'  # Shorts
            ]
        },
        # Instagram
        'instagram': {
            'domains': ['www.instagram.com', 'instagram.com'],
            'patterns': [
                r'instagram\.com\/(?:p|reel)\/([^/?]+)',  # Posts and Reels
                r'instagram\.com\/stories\/([^/?]+)\/([^/?]+)'  # Stories
            ]
        },
        # Facebook
        'facebook': {
            'domains': ['www.facebook.com', 'facebook.com', 'fb.com', 'fb.watch', 'm.facebook.com'],
            'patterns': [
                r'facebook\.com\/[^\/]+\/videos\/([^/?]+)',
                r'facebook\.com\/watch\/?\?v=([^&]+)',
                r'fb\.watch\/([^/?]+)'
            ]
        },
        # Twitter/X
        'twitter': {
            'domains': ['www.twitter.com', 'twitter.com', 'x.com', 'www.x.com'],
            'patterns': [
                r'twitter\.com\/[^\/]+\/status\/(\d+)',
                r'x\.com\/[^\/]+\/status\/(\d+)'
            ]
        },
        # TikTok
        'tiktok': {
            'domains': ['www.tiktok.com', 'tiktok.com', 'vm.tiktok.com'],
            'patterns': [
                r'tiktok\.com\/@([^\/]+)\/video\/(\d+)',
                r'tiktok\.com\/t\/([^/?]+)'
            ]
        },
        # Reddit
        'reddit': {
            'domains': ['www.reddit.com', 'reddit.com', 'v.redd.it'],
            'patterns': [
                r'reddit\.com\/r\/[^\/]+\/comments\/([^\/]+)',
                r'v\.redd\.it\/([^/?]+)'
            ]
        }
    }
    
    # Check if the hostname belongs to any supported platform
    for platform, config in patterns.items():
        if any(domain == hostname for domain in config['domains']):
            # Check if URL matches any pattern for this platform
            for pattern in config['patterns']:
                if re.search(pattern, url):
                    return True
    
    return False

def get_platform(url):
    """Determine which platform the URL is from."""
    parsed_url = urlparse(url)
    hostname = parsed_url.netloc.lower()
    
    # YouTube
    if hostname in ['www.youtube.com', 'youtube.com', 'youtu.be', 'm.youtube.com']:
        return 'youtube'
    
    # Instagram
    if hostname in ['www.instagram.com', 'instagram.com']:
        return 'instagram'
    
    # Facebook
    if hostname in ['www.facebook.com', 'facebook.com', 'fb.com', 'fb.watch', 'm.facebook.com']:
        return 'facebook'
    
    # Twitter/X
    if hostname in ['www.twitter.com', 'twitter.com', 'x.com', 'www.x.com']:
        return 'twitter'
    
    # TikTok
    if hostname in ['www.tiktok.com', 'tiktok.com', 'vm.tiktok.com']:
        return 'tiktok'
    
    # Reddit
    if hostname in ['www.reddit.com', 'reddit.com', 'v.redd.it']:
        return 'reddit'
    
    return 'unknown'

def get_video_info(url):
    """Get video information using yt-dlp."""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
        'forcejson': True,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'formats': [
                    {
                        'format_id': format.get('format_id'),
                        'ext': format.get('ext'),
                        'resolution': f"{format.get('width', 'unknown')}x{format.get('height', 'unknown')}",
                        'filesize': format.get('filesize'),
                        'format_note': format.get('format_note', ''),
                        'vcodec': format.get('vcodec', 'unknown'),
                        'acodec': format.get('acodec', 'unknown'),
                    }
                    for format in info.get('formats', [])
                    if format.get('ext') in ['mp4', 'webm', 'mkv', 'm4a', 'mp3'] 
                    and not format.get('format_note') == 'storyboard'
                ]
            }
        except yt_dlp.utils.DownloadError as e:
            logging.error(f"Error extracting info: {e}")
            return None

@app.route('/')
def index():
    # Get the latest downloads from the database
    downloads = Download.query.order_by(Download.download_date.desc()).limit(5).all()
    return render_template('index.html', downloads=downloads)
    
@app.route('/history')
def history():
    """Display the complete download history"""
    # Get all downloads from the database
    downloads = Download.query.order_by(Download.download_date.desc()).all()
    return render_template('history.html', downloads=downloads)
    
@app.route('/api/downloads/<int:download_id>', methods=['DELETE'])
def delete_download(download_id):
    """Delete a download record from the database"""
    download = Download.query.get_or_404(download_id)
    
    try:
        db.session.delete(download)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Download record deleted successfully'})
    except Exception as e:
        logging.error(f"Error deleting download: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/info', methods=['POST'])
def get_info():
    url = request.form.get('url')
    
    if not url:
        return jsonify({'error': 'No URL provided'}), 400
    
    if not validate_url(url):
        platform_names = "YouTube, Instagram, Facebook, Twitter, TikTok, Reddit, and more"
        return jsonify({'error': f'Invalid URL. We support {platform_names}'}), 400
    
    try:
        platform = get_platform(url)
        logging.info(f"Processing URL from platform: {platform}")
        
        video_info = get_video_info(url)
        if not video_info:
            return jsonify({'error': 'Could not retrieve video information'}), 400
            
        # Add platform info to the response
        video_info['platform'] = platform
        
        # Define the standard resolutions we want to offer
        target_resolutions = [144, 240, 360, 480, 720, 1080, 1440, 2160]
        resolution_formats = {res: None for res in target_resolutions}
        
        # Collect all formats with audio+video
        av_formats = []
        for format in video_info['formats']:
            # Check if the format has both video and audio
            has_video = format['vcodec'] != 'none'
            has_audio = format['acodec'] != 'none'
            
            # Skip formats without both video and audio, or with unknown resolution
            if not (has_video and has_audio) or format['resolution'] == 'unknownxunknown':
                continue
                
            # Only consider MP4 and WebM formats
            if format['ext'] not in ['mp4', 'webm']:
                continue
                
            av_formats.append(format)
        
        # For each target resolution, find the best format that matches
        for format in av_formats:
            # Extract height from resolution
            try:
                height = int(format['resolution'].split('x')[1])
                # Find the closest standard resolution that is >= the actual height
                for res in target_resolutions:
                    if height <= res:
                        # If this format is better than what we already have for this resolution
                        if resolution_formats[res] is None:
                            resolution_formats[res] = format
                        break
            except (ValueError, IndexError):
                continue
        
        # Convert to list of formats with clear labels
        formats = []
        for res, format in sorted(resolution_formats.items(), key=lambda x: x[0]):
            if format:
                # Create a clean format object with clear labeling
                format['format_note'] = f"{res}p"
                formats.append(format)
        
        # If no matching formats found, use best video + best audio combo
        if not formats:
            formats.append({
                'format_id': 'best[ext=mp4]/best',
                'ext': 'mp4',
                'resolution': 'Best quality',
                'format_note': 'Best quality',
                'vcodec': 'h264',
                'acodec': 'aac',
            })
        
        # Add audio-only option
        formats.append({
            'format_id': 'bestaudio',
            'ext': 'mp3',
            'resolution': 'Audio only',
            'format_note': 'MP3 Audio',
            'vcodec': 'none',
            'acodec': 'mp3',
        })
        
        video_info['formats'] = formats
        return jsonify(video_info)
    except Exception as e:
        logging.error(f"Error in /info: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')
    format_id = request.form.get('format')
    
    if not url or not format_id:
        return jsonify({'error': 'URL and format are required'}), 400
    
    if not validate_url(url):
        platform_names = "YouTube, Instagram, Facebook, Twitter, TikTok, Reddit, and more"
        return jsonify({'error': f'Invalid URL. We support {platform_names}'}), 400
        
    # Get platform info
    platform = get_platform(url)
    logging.info(f"Downloading from platform: {platform}")
    
    try:
        # Generate a unique ID for this download
        download_id = str(uuid.uuid4())
        output_path = os.path.join(temp_dir, f"{download_id}")
        
        # Extract video info 
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            video_id = info.get('id')
            title = info.get('title')
            thumbnail = info.get('thumbnail')
            duration = info.get('duration')
            
            # Find selected format
            selected_format = None
            filesize = None
            for format in info.get('formats', []):
                if format.get('format_id') == format_id:
                    selected_format = format
                    filesize = format.get('filesize')
                    ext = format.get('ext', 'mp4')
                    break
            else:
                ext = 'mp4'  # default
        
        # Common options
        ydl_opts = {
            'outtmpl': f"{output_path}.%(ext)s",
            # Use ffmpeg for format conversion and merging
            'postprocessor_args': ['-threads', '4'],
            'keepvideo': True  # Keep the original video file
        }
        
        # Set up options based on format
        if format_id == 'bestaudio':
            # Get best audio and convert to MP3
            ydl_opts.update({
                'format': 'bestaudio/best',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            })
            ext = 'mp3'
        elif format_id == 'best[ext=mp4]/best':
            # For best quality, let yt-dlp choose the best format with audio and video
            ydl_opts.update({
                'format': 'best[ext=mp4]/best',  # Prefer MP4, fallback to best available
                'merge_output_format': 'mp4',
            })
            ext = 'mp4'
        else:
            # For specific format IDs
            ydl_opts.update({
                'format': format_id,
                'merge_output_format': ext,
            })
            
        # Add additional options for ensuring audio+video
        if ext != 'mp3':
            # Check if the chosen format has audio - if not, let yt-dlp find and merge audio
            if selected_format and selected_format.get('acodec') == 'none':
                # Get video+audio by adding a best audio format
                ydl_opts['format'] = f"{format_id}+bestaudio[ext=m4a]/best"
                ydl_opts['merge_output_format'] = 'mp4'
        
        # Store the download info
        downloads[download_id] = {
            'url': url,
            'format_id': format_id,
            'output_file': f"{output_path}.{ext}",
            'ext': ext,
            'status': 'downloading'
        }
        
        # Start download with enhanced options
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        # Update status
        downloads[download_id]['status'] = 'completed'
        
        # Calculate actual file size 
        actual_file_size = os.path.getsize(downloads[download_id]['output_file']) if os.path.exists(downloads[download_id]['output_file']) else filesize
        
        # Save to database with platform info
        download_record = Download(
            video_id=video_id,
            title=title,
            format=f"{format_id} ({ext})",
            platform=platform,  # Add platform info
            filesize=actual_file_size,
            thumbnail_url=thumbnail,
            duration=duration
        )
        db.session.add(download_record)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'download_id': download_id,
            'message': 'Download completed successfully!'
        })
    except Exception as e:
        logging.error(f"Download error: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/get_file/<download_id>')
def get_file(download_id):
    if download_id not in downloads:
        abort(404)
    
    download_info = downloads[download_id]
    
    if download_info['status'] != 'completed':
        abort(400, description="Download not completed yet")
    
    try:
        # Get platform
        platform = get_platform(download_info['url'])
        
        # Get original filename from the video
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}) as ydl:
            info = ydl.extract_info(download_info['url'], download=False)
            title = info.get('title', 'video')
        
        # Clean the title to make it a valid filename
        title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')
        # Add platform name to the filename for clarity
        filename = f"{platform}_{title}.{download_info['ext']}"
        
        return send_file(
            download_info['output_file'],
            as_attachment=True,
            download_name=filename,
            mimetype=f"{'audio' if download_info['ext'] == 'mp3' else 'video'}/{download_info['ext']}"
        )
    except Exception as e:
        logging.error(f"Error sending file: {e}")
        abort(500, description=str(e))

# Clean up temporary files when the app exits
import atexit
import shutil

@atexit.register
def cleanup():
    shutil.rmtree(temp_dir, ignore_errors=True)
    logging.debug(f"Cleaned up temporary directory {temp_dir}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
