# app.py  
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
import os
import logging
import threading
import time
import sqlite3
from datetime import datetime
from db_models import Database
from worker import LinkWorker
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("link_system.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = ''
db = Database()
worker = LinkWorker()


# Add datetime format filter
@app.template_filter('format_datetime')
def format_datetime(value, format='%d %b %Y %H:%M'):
    if value:
        try:
            # First remove microseconds if present
            if '.' in value:
                value = value.split('.')[0]
                # Parse the datetime string and format it
            dt = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
            return dt.strftime(format)
        except Exception as e:
            logger.error(f"Error formatting datetime: {e}")
            return value
    return ''


# Start background worker thread
def background_worker():
    while True:
        try:
            worker.process_pending_accesses()
            time.sleep(60)  # Check every minute
        except Exception as e:
            logger.error(f"Error in background worker: {e}")
            time.sleep(60)  # Wait a bit before retrying


# Create worker thread
worker_thread = threading.Thread(target=background_worker, daemon=True)


@app.route('/')
def index():
    """Dashboard home page"""
    active_links = db.get_active_links()
    return render_template('index.html', links=active_links)


@app.route('/add_link', methods=['POST'])
def add_link():
    """Add a new link and extract metadata immediately"""
    try:
        url = request.form.get('url', '').strip()
        if not url:
            return jsonify({"error": "URL cannot be empty"}), 400

            # Check if the URL contains a directory listing and extract direct links
        links_to_add = worker.check_and_extract_links(url)

        # Keep track of how many links were successfully added
        added_count = 0
        duplicate_count = 0

        for link_url in links_to_add:
            # Check if link already exists
            conn = db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM links WHERE url = ?", (link_url,))
            existing = cursor.fetchone()

            if existing:
                # Link already exists, skip it
                duplicate_count += 1
                logger.info(f"Skipping duplicate link: {link_url}")
                continue

                # Add link to database
            link_id = db.add_link(link_url)
            added_count += 1

            # Extract metadata immediately in a non-blocking way
            threading.Thread(target=worker.extract_metadata, args=(link_url, link_id)).start()

            # Create appropriate flash message
        if added_count > 0 and duplicate_count > 0:
            flash(f"Added {added_count} new links. Skipped {duplicate_count} duplicate links.", "success")
        elif added_count > 0:
            flash(f"Added {added_count} new links successfully.", "success")
        else:
            flash(f"No new links added. All {duplicate_count} links already exist in the database.", "warning")

        return redirect(url_for('index'))
    except Exception as e:
        logger.error(f"Error adding link: {e}")
        flash(f"Error adding link: {str(e)}", "danger")
        return redirect(url_for('index'))

@app.route('/delete_link/<int:link_id>', methods=['POST'])
def delete_link(link_id):
    """Delete a link and its logs"""
    try:
        success = db.delete_link(link_id)
        if success:
            return jsonify({"success": True})
        else:
            return jsonify({"error": "Failed to delete link"}), 400
    except Exception as e:
        logger.error(f"Error deleting link {link_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/force_run/<int:link_id>', methods=['POST'])
def force_run(link_id):
    """Force run the worker process for a specific link"""
    try:
        # Get the URL for this link ID
        conn = db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT url FROM links WHERE id = ?", (link_id,))
        result = cursor.fetchone()

        if not result:
            return jsonify({"error": "Link not found"}), 404

        url = result['url']

        # Run access in a separate thread
        threading.Thread(target=worker.access_link, args=(link_id, url)).start()

        return jsonify({"success": True, "message": "Access process started"})
    except Exception as e:
        logger.error(f"Error in force run for link {link_id}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/links')
def get_links():
    """API endpoint to get active links"""
    try:
        active_links = db.get_active_links()
        # Convert Row objects to dictionaries
        links_data = [dict(link) for link in active_links]
        return jsonify({"links": links_data})
    except Exception as e:
        logger.error(f"Error getting links: {e}")
        return jsonify({"error": str(e)}), 500


@app.route('/query', methods=['GET', 'POST'])
def query():
    """Page for executing raw SQL queries"""
    result = None
    query_text = ""
    error = None

    if request.method == 'POST':
        query_text = request.form.get('query', '').strip()
        if query_text:
            try:
                result = db.execute_raw_query(query_text)
                # If result is a list of Row objects, convert to list of dicts
                if isinstance(result, list) and result and isinstance(result[0], sqlite3.Row):
                    result = [dict(row) for row in result]
            except Exception as e:
                error = str(e)

    return render_template('query.html', result=result, query=query_text, error=error)


if __name__ == '__main__':
    # Start the background worker in a separate thread
    worker_thread.start()

    # Run the Flask application  
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=False)