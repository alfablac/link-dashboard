# db_models.py  
import sqlite3
import os
import logging
from datetime import datetime, timedelta

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


class Database:
    def __init__(self, db_name="links.db"):
        self.db_name = db_name
        self.conn = None
        self.init_db()

    def get_connection(self):
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_name, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def init_db(self):
        """Initialize the database with required tables"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Create links table with new columns for cycle tracking
            cursor.execute('''  
            CREATE TABLE IF NOT EXISTS links (  
                id INTEGER PRIMARY KEY AUTOINCREMENT,  
                url TEXT UNIQUE NOT NULL,  
                date_added TIMESTAMP NOT NULL,  
                current_cycle_start TIMESTAMP NOT NULL,  
                current_cycle_end TIMESTAMP NOT NULL,  
                total_views INTEGER DEFAULT 0,  
                current_period_views INTEGER DEFAULT 0,  
                current_cycle INTEGER DEFAULT 1,  
                active BOOLEAN DEFAULT 1,  
                filename TEXT,  
                file_details TEXT  
            )  
            ''')

            # Check if we need to update the schema for existing tables
            cursor.execute("PRAGMA table_info(links)")
            columns = [column[1] for column in cursor.fetchall()]

            # Add new columns if they don't exist
            if "filename" not in columns:
                cursor.execute("ALTER TABLE links ADD COLUMN filename TEXT")
                logger.info("Added filename column to links table")

            if "file_details" not in columns:
                cursor.execute("ALTER TABLE links ADD COLUMN file_details TEXT")
                logger.info("Added file_details column to links table")

            if "current_cycle" not in columns:
                cursor.execute("ALTER TABLE links ADD COLUMN current_cycle INTEGER DEFAULT 1")
                logger.info("Added current_cycle column to links table")

            if "current_cycle_start" not in columns:
                cursor.execute("ALTER TABLE links ADD COLUMN current_cycle_start TIMESTAMP")
                # Update existing records
                cursor.execute("UPDATE links SET current_cycle_start = date_added WHERE current_cycle_start IS NULL")
                logger.info("Added current_cycle_start column to links table")

            if "current_cycle_end" not in columns:
                cursor.execute("ALTER TABLE links ADD COLUMN current_cycle_end TIMESTAMP")
                # Update existing records
                cursor.execute(
                    "UPDATE links SET current_cycle_end = datetime(date_added, '+45 days') WHERE current_cycle_end IS NULL")
                logger.info("Added current_cycle_end column to links table")

                # Drop end_date column if it exists (we're replacing with cycle-based approach)
            if "end_date" in columns:
                # SQLite doesn't support DROP COLUMN directly, so we need to do a workaround
                # Create a temporary table with the new schema
                cursor.execute('''  
                CREATE TABLE links_temp (  
                    id INTEGER PRIMARY KEY AUTOINCREMENT,  
                    url TEXT UNIQUE NOT NULL,  
                    date_added TIMESTAMP NOT NULL,  
                    current_cycle_start TIMESTAMP NOT NULL,  
                    current_cycle_end TIMESTAMP NOT NULL,  
                    total_views INTEGER DEFAULT 0,  
                    current_period_views INTEGER DEFAULT 0,  
                    current_cycle INTEGER DEFAULT 1,  
                    active BOOLEAN DEFAULT 1,  
                    filename TEXT,  
                    file_details TEXT  
                )  
                ''')

                # Copy data from the old table to the new one
                cursor.execute('''  
                INSERT INTO links_temp(id, url, date_added, current_cycle_start, current_cycle_end,   
                                      total_views, current_period_views, current_cycle, active, filename, file_details)  
                SELECT id, url, date_added, current_cycle_start, current_cycle_end,  
                       total_views, current_period_views, current_cycle, active, filename, file_details  
                FROM links  
                ''')

                # Drop the old table
                cursor.execute("DROP TABLE links")

                # Rename the new table to the original name
                cursor.execute("ALTER TABLE links_temp RENAME TO links")

                logger.info("Removed end_date column and restructured links table")

                # Create access logs table
            cursor.execute('''  
            CREATE TABLE IF NOT EXISTS access_logs (  
                id INTEGER PRIMARY KEY AUTOINCREMENT,  
                link_id INTEGER NOT NULL,  
                access_time TIMESTAMP NOT NULL,  
                proxy_used TEXT,  
                status_code INTEGER,  
                error_message TEXT,  
                cycle INTEGER NOT NULL DEFAULT 1,  
                FOREIGN KEY (link_id) REFERENCES links (id)  
            )  
            ''')

            # Add cycle column to access_logs if it doesn't exist
            cursor.execute("PRAGMA table_info(access_logs)")
            columns = [column[1] for column in cursor.fetchall()]
            if "cycle" not in columns:
                cursor.execute("ALTER TABLE access_logs ADD COLUMN cycle INTEGER NOT NULL DEFAULT 1")
                logger.info("Added cycle column to access_logs table")

                # Create proxy usage table to track which proxies have been used for each link in each cycle
            cursor.execute('''  
            CREATE TABLE IF NOT EXISTS proxy_usage (  
                id INTEGER PRIMARY KEY AUTOINCREMENT,  
                link_id INTEGER NOT NULL,  
                proxy TEXT NOT NULL,  
                cycle INTEGER NOT NULL,  
                used_at TIMESTAMP NOT NULL,  
                FOREIGN KEY (link_id) REFERENCES links (id),  
                UNIQUE(link_id, proxy, cycle)  
            )  
            ''')

            conn.commit()
            logger.info("Database initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing database: {e}")
            raise

    def add_link(self, url):
        """Add a new link to the database"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            now = datetime.now()
            cycle_end = now + timedelta(days=45)

            cursor.execute(
                "INSERT INTO links (url, date_added, current_cycle_start, current_cycle_end, current_cycle) VALUES (?, ?, ?, ?, ?)",
                (url, now, now, cycle_end, 1)
            )
            conn.commit()
            link_id = cursor.lastrowid
            logger.info(f"Added new link: {url} with ID {link_id}")
            return link_id
        except sqlite3.IntegrityError:
            logger.warning(f"Link already exists: {url}")
            cursor.execute("SELECT id FROM links WHERE url = ?", (url,))
            return cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"Error adding link: {e}")
            raise

    def increment_link_views(self, link_id):
        """
        Increment the view count for a link.
        If current_period_views reaches 100, increment cycle and reset views.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # First, get the current link information
            cursor.execute("""  
                SELECT current_period_views, current_cycle, current_cycle_end   
                FROM links   
                WHERE id = ?  
            """, (link_id,))

            link_data = cursor.fetchone()
            if not link_data:
                logger.error(f"Failed to increment views: Link ID {link_id} not found")
                return False

            current_views = link_data['current_period_views']
            current_cycle = link_data['current_cycle']
            cycle_end = datetime.strptime(link_data['current_cycle_end'], '%Y-%m-%d %H:%M:%S.%f') if link_data[
                'current_cycle_end'] else None

            # Check if we need to start a new cycle (current cycle ended)
            now = datetime.now()
            if cycle_end and now > cycle_end:
                # Start a new cycle
                new_cycle_start = now
                new_cycle_end = now + timedelta(days=60)  # 60-day cycle

                cursor.execute("""  
                    UPDATE links   
                    SET current_cycle = ?,   
                        current_period_views = 1,  
                        total_views = total_views + 1,  
                        current_cycle_start = ?,  
                        current_cycle_end = ?  
                    WHERE id = ?  
                """, (current_cycle + 1, new_cycle_start, new_cycle_end, link_id))

                logger.info(f"Started new cycle {current_cycle + 1} for link ID {link_id}")

                # Regular increment within current cycle
            else:
                new_views = current_views + 1

                # Check if we've completed all 100 views for this cycle
                if new_views >= 100:
                    # When reaching 100 views, mark cycle as completed but keep the end date
                    cursor.execute("""  
                        UPDATE links   
                        SET current_period_views = ?,  
                            total_views = total_views + 1  
                        WHERE id = ?  
                    """, (new_views, link_id))
                    logger.info(f"Completed all 100 views for link ID {link_id} in cycle {current_cycle}")
                else:
                    # Normal increment
                    cursor.execute("""  
                        UPDATE links   
                        SET current_period_views = ?,  
                            total_views = total_views + 1  
                        WHERE id = ?  
                    """, (new_views, link_id))
                    logger.info(f"Incremented views for link ID {link_id} to {new_views} in cycle {current_cycle}")

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error incrementing views for link ID {link_id}: {e}")
            return False

    def delete_link(self, link_id):
        """Delete a link and its associated access logs and proxy usage records"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # First, check if the link exists
            cursor.execute("SELECT url FROM links WHERE id = ?", (link_id,))
            result = cursor.fetchone()
            if not result:
                logger.warning(f"Attempted to delete non-existent link with ID {link_id}")
                return False

                # Delete associated access logs
            cursor.execute("DELETE FROM access_logs WHERE link_id = ?", (link_id,))

            # Delete associated proxy usage records
            cursor.execute("DELETE FROM proxy_usage WHERE link_id = ?", (link_id,))

            # Delete the link
            cursor.execute("DELETE FROM links WHERE id = ?", (link_id,))

            conn.commit()
            logger.info(f"Deleted link with ID {link_id} (URL: {result['url']})")
            return True
        except Exception as e:
            logger.error(f"Error deleting link with ID {link_id}: {e}")
            conn.rollback()
            return False

    def update_link_info(self, link_id, filename, file_details):
        """Update filename and file details for a link"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute(
                "UPDATE links SET filename = ?, file_details = ? WHERE id = ?",
                (filename, file_details, link_id)
            )
            conn.commit()
            logger.info(f"Updated info for link ID {link_id}: filename='{filename}', details='{file_details}'")
            return True
        except Exception as e:
            logger.error(f"Error updating link info: {e}")
            raise

    def get_active_links(self):
        """Get all active links with cycle information"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            now = datetime.now()

            # First, check if any links need to move to the next cycle
            cursor.execute('''  
            SELECT id, url, current_cycle, current_cycle_end   
            FROM links   
            WHERE active = 1 AND current_cycle_end < ?  
            ''', (now,))

            links_to_update = cursor.fetchall()

            # Update links that have completed a cycle
            for link in links_to_update:
                new_cycle = link['current_cycle'] + 1
                new_cycle_start = datetime.strptime(link['current_cycle_end'], '%Y-%m-%d %H:%M:%S.%f')
                new_cycle_end = new_cycle_start + timedelta(days=45)

                cursor.execute('''  
                UPDATE links   
                SET current_cycle = ?,   
                    current_period_views = 0,   
                    current_cycle_start = ?,   
                    current_cycle_end = ?   
                WHERE id = ?  
                ''', (new_cycle, new_cycle_start, new_cycle_end, link['id']))

                logger.info(f"Link ID {link['id']} moved to cycle {new_cycle}")

            if links_to_update:
                conn.commit()

                # Now get all active links with days remaining in current cycle
            cursor.execute('''  
            SELECT   
                id, url, date_added, current_cycle_start, current_cycle_end,   
                total_views, current_period_views, current_cycle,  
                ROUND(julianday(current_cycle_end) - julianday(?), 1) as days_remaining,  
                filename, file_details  
            FROM links  
            WHERE active = 1  
            ORDER BY current_cycle_end ASC  
            ''', (now,))

            return cursor.fetchall()
        except Exception as e:
            logger.error(f"Error fetching active links: {e}")
            raise

    def log_access(self, link_id, proxy_used, status_code=None, error_message=None):
        """Log an access attempt and track proxy usage"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            now = datetime.now()

            # Get the current cycle for this link
            cursor.execute("SELECT current_cycle FROM links WHERE id = ?", (link_id,))
            result = cursor.fetchone()
            if not result:
                logger.error(f"Attempted to log access for non-existent link ID {link_id}")
                return False

            current_cycle = result['current_cycle']

            # Log the access
            cursor.execute(
                "INSERT INTO access_logs (link_id, access_time, proxy_used, status_code, error_message, cycle) VALUES (?, ?, ?, ?, ?, ?)",
                (link_id, now, proxy_used, status_code, error_message, current_cycle)
            )

            # If access was successful and used a proxy, record it in proxy_usage
            if status_code and 200 <= status_code < 300 and proxy_used:
                try:
                    cursor.execute(
                        "INSERT INTO proxy_usage (link_id, proxy, cycle, used_at) VALUES (?, ?, ?, ?)",
                        (link_id, proxy_used, current_cycle, now)
                    )
                except sqlite3.IntegrityError:
                    # This proxy was already used for this link in this cycle
                    # This shouldn't happen with our logic, but handle it just in case
                    logger.warning(
                        f"Proxy {proxy_used} was already recorded for link ID {link_id} in cycle {current_cycle}")

                    # Update view counts
                cursor.execute(
                    "UPDATE links SET total_views = total_views + 1, current_period_views = current_period_views + 1 WHERE id = ?",
                    (link_id,)
                )

            conn.commit()
            logger.info(f"Logged access for link_id {link_id} with status {status_code} in cycle {current_cycle}")
            return True
        except Exception as e:
            logger.error(f"Error logging access: {e}")
            conn.rollback()
            return False

    def record_proxy_usage(self, link_id, proxy_url):
        """Record that a proxy was used for a specific link"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Record the proxy usage with timestamp
            cursor.execute("""  
                INSERT INTO proxy_usage (link_id, proxy_url, used_at)  
                VALUES (?, ?, datetime('now'))  
            """, (link_id, proxy_url))

            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error recording proxy usage: {e}")
            return False

    def get_unused_proxies_for_link(self, link_id, all_proxies, cooldown_hours=24):
        """Get proxies that haven't been used for this link within the cooldown period"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Get proxies used for this link within the cooldown period
            cursor.execute("""  
                SELECT proxy_url FROM proxy_usage  
                WHERE link_id = ? AND used_at > datetime('now', ?)  
            """, (link_id, f'-{cooldown_hours} hours'))

            used_proxies = {row['proxy_url'] for row in cursor.fetchall()}

            # Return proxies that haven't been used within the cooldown period
            unused_proxies = [proxy for proxy in all_proxies if proxy not in used_proxies]

            if not unused_proxies:
                logger.warning(f"All proxies have been used for link {link_id} within the past {cooldown_hours} hours")
                # If all are used, return all proxies - we'll have to reuse one
                return all_proxies

            return unused_proxies
        except Exception as e:
            logger.error(f"Error getting unused proxies: {e}")
            return all_proxies  # Return all proxies in case of error

    def execute_raw_query(self, query):
        """Execute a raw SQL query and return results"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(query)

            if query.strip().upper().startswith(("SELECT", "PRAGMA")):
                return cursor.fetchall()
            else:
                conn.commit()
                return {"message": "Query executed successfully", "rows_affected": cursor.rowcount}
        except Exception as e:
            logger.error(f"Error executing raw query: {e}")
            raise