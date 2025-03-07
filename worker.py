# worker.py  
import random
import time
import os
import math
import logging
from datetime import datetime, timedelta
from db_models import Database
from curl_cffi import requests as curl_requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


class LinkWorker:
    def __init__(self):
        self.db = Database()
        # Get proxies from hardcoded list
        self.proxies = self._get_proxies()
        if not self.proxies:
            logger.warning("No proxies were loaded from the list.")

    def _get_proxies(self):
        """Get proxies from HTTP_PROXIES environment variable"""
        try:
            proxy_string = os.environ.get('HTTP_PROXIES', '')
            if not proxy_string:
                logger.warning("HTTP_PROXIES environment variable is not set")
                return []

                # Split the string into individual proxies
            # Handles both comma-separated and newline-separated formats
            proxies = [p.strip() for p in proxy_string.replace('\n', ',').split(',') if p.strip()]

            logger.info(f"Loaded {len(proxies)} proxies from HTTP_PROXIES environment variable")
            return proxies
        except Exception as e:
            logger.error(f"Error parsing HTTP_PROXIES: {e}")
            return []

    def _calculate_access_times(self, start_date, end_date, total_accesses=100):
        """
        Calculate access times using an exponential function
        More frequent at the beginning, less frequent at the end
        """
        total_seconds = (end_date - start_date).total_seconds()

        # Generate exponentially distributed points
        times = []
        for i in range(total_accesses):
            # Exponential distribution factor (higher means more skewed to beginning)
            factor = 3.0
            x = i / (total_accesses - 1)  # Normalized index (0 to 1)
            y = (1 - math.exp(-factor * (1 - x))) / (1 - math.exp(-factor))

            # Calculate seconds from start
            seconds_from_start = y * total_seconds
            access_time = start_date + timedelta(seconds=seconds_from_start)
            times.append(access_time)

        return sorted(times)

    def check_and_extract_links(self, url):
        """
        Check if URL contains a table with class="fs" and extract direct links.
        Returns a list of links. If no fs table, returns a list with just the original URL.
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
                "Accept": "*/*",
                "Accept-Language": "pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
                "TE": "trailers",
            }

            logger.info(f"Checking URL for directory listing: {url}")

            # Visit the page
            session = curl_requests.Session()
            response = session.get(
                url,
                headers=headers,
                timeout=30,
                impersonate="firefox135"
            )

            # Parse the HTML
            soup = BeautifulSoup(response.text, 'html.parser')

            # Check if there's a table with class="fs"
            fs_table = soup.find('table', class_='fs')

            if fs_table:
                logger.info(f"Found directory listing table in {url}")

                # Find all links with target="_blank" and non-empty href
                direct_links = []
                for link in fs_table.find_all('a', target='_blank', href=True):
                    href = link.get('href')
                    if href and not href.startswith('#') and not href == '/':
                        # Make sure the URL is absolute
                        if not href.startswith('http'):
                            # Parse the original URL to get the base
                            parsed_url = urlparse(url)
                            base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

                            # If href starts with /, it's relative to the domain root
                            if href.startswith('/'):
                                full_url = f"{base_url}{href}"
                            else:
                                # Otherwise it's relative to the current path
                                path_parts = parsed_url.path.split('/')
                                # Remove the last part if it's not empty
                                if path_parts[-1]:
                                    path_parts = path_parts[:-1]
                                base_path = '/'.join(path_parts)
                                full_url = f"{base_url}{base_path}/{href}"

                            direct_links.append(full_url)
                        else:
                            direct_links.append(href)

                if direct_links:
                    logger.info(f"Extracted {len(direct_links)} direct links from {url}")
                    return direct_links

                    # If no fs table or no links found, return the original URL
            return [url]
        except Exception as e:
            logger.error(f"Error checking URL {url}: {e}")
            # Return the original URL if there's an error
            return [url]

    def extract_metadata(self, url, link_id=None):
        """Extract filename and file details from the link page"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
                "Accept": "*/*",
                "Accept-Language": "pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
                "TE": "trailers",
            }

            logger.info(f"Extracting metadata from URL: {url}")

            # Visit the page
            session = curl_requests.Session()
            response = session.get(
                url,
                headers=headers,
                timeout=30,
                impersonate="firefox135"
            )

            # Parse the HTML and find metadata
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract filename from span with class "text-2xl"
            filename_span = soup.find('span', class_='text-2xl')
            filename = filename_span.get_text().strip() if filename_span else "Unknown Filename"

            # Extract file details from the 3rd list item sibling to this span
            file_details = "No details available"
            if filename_span:
                # Find the parent element and then the ul tag
                parent = filename_span.parent
                ul_tag = parent.find('ul')
                if ul_tag:
                    # Get the 3rd list item
                    li_items = ul_tag.find_all('li')
                    if len(li_items) >= 3:
                        file_details = li_items[2].get_text().strip()

            logger.info(f"Extracted filename: '{filename}' and details: '{file_details}'")

            # Store the data if link_id is provided
            if link_id:
                self.db.update_link_info(link_id, filename, file_details)

            return {"filename": filename, "file_details": file_details}

        except Exception as e:
            logger.error(f"Error extracting metadata from {url}: {e}")
            return {"filename": "Error extracting", "file_details": str(e)}

    def schedule_link_accesses(self, link_id, url):
        """
        Schedule accesses for a link over current 45 days cycle
        Returns a list of datetime objects when the link should be accessed
        """
        conn = self.db.get_connection()
        cursor = conn.cursor()

        cursor.execute("""  
            SELECT current_cycle_start, current_cycle_end   
            FROM links   
            WHERE id = ?  
        """, (link_id,))

        link_data = cursor.fetchone()

        if not link_data:
            logger.error(f"Link ID {link_id} not found in database")
            return []

        start_date = datetime.strptime(link_data['current_cycle_start'], '%Y-%m-%d %H:%M:%S.%f')
        end_date = datetime.strptime(link_data['current_cycle_end'], '%Y-%m-%d %H:%M:%S.%f')

        # Calculate the next 100 access times for this cycle
        access_times = self._calculate_access_times(start_date, end_date)

        logger.info(f"Scheduled {len(access_times)} accesses for link {url} (ID: {link_id})")
        return access_times

    def access_link(self, link_id, url):
        """Access a link and follow the download button with proxy rotation"""
        # Get proxies that haven't been used for this link in the current cycle
        unused_proxies = self.db.get_unused_proxies_for_link(link_id)

        # Choose a proxy
        proxy = None
        proxies = None

        if unused_proxies:
            proxy = random.choice(unused_proxies)
            proxies = {"http": proxy, "https": proxy}
            logger.info(f"Using unused proxy {proxy} for link ID {link_id}")
        elif self.proxies:
            # If all proxies have been used, reuse one of the existing proxies
            proxy = random.choice(self.proxies)
            proxies = {"http": proxy, "https": proxy}
            logger.warning(f"All proxies have been used for link ID {link_id} in this cycle. Reusing {proxy}")
        else:
            logger.warning(f"No proxies available for link ID {link_id}. Using direct connection.")

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
                "Accept": "*/*",
                "Accept-Language": "pt-BR,pt;q=0.8,en-US;q=0.5,en;q=0.3",
                "Accept-Encoding": "gzip, deflate, br, zstd",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "Pragma": "no-cache",
                "Cache-Control": "no-cache",
                "TE": "trailers",
            }

            # Add some randomization to appear more human-like
            time.sleep(random.uniform(1, 3))

            logger.info(f"Accessing URL: {url}")

            # Step 1: Visit the main page
            session = curl_requests.Session()
            if proxies:
                session.proxies = proxies

            response = session.get(
                url,
                headers=headers,
                timeout=30,
                impersonate="firefox135"
            )

            # Step 2: Parse the HTML and find the download button and file information
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract filename from span with class "text-2xl"
            filename_span = soup.find('span', class_='text-2xl')
            filename = filename_span.get_text().strip() if filename_span else "Unknown Filename"

            # Extract file details from the 3rd list item sibling to this span
            file_details = "No details available"
            if filename_span:
                # Find the parent element and then the ul tag
                parent = filename_span.parent
                ul_tag = parent.find('ul')
                if ul_tag:
                    # Get the 3rd list item
                    li_items = ul_tag.find_all('li')
                    if len(li_items) >= 3:
                        file_details = li_items[2].get_text().strip()

                        # Store the filename and file details in the database
            self.db.update_link_info(link_id, filename, file_details)
            logger.info(f"Extracted filename: '{filename}' and details: '{file_details}'")

            # Find the download button
            download_button = soup.find('a', class_=lambda c: c and 'link-button' in c and 'gay-button' in c)

            if not download_button:
                logger.error(f"Could not find download button on {url}")
                self.db.log_access(link_id, proxy, response.status_code, "Download button not found")
                return False

            download_url = download_button.get('hx-get')
            if not download_url:
                logger.error(f"Download button found but no href attribute on {url}")
                self.db.log_access(link_id, proxy, response.status_code, "No href in download button")
                return False

                # Make sure the URL is absolute
            if not download_url.startswith('http'):
                if download_url.startswith('/'):
                    # Extract domain from original URL
                    parts = url.split('/')
                    base_url = f"{parts[0]}//{parts[2]}"
                    download_url = base_url + download_url
                else:
                    # Relative URL, append to the path
                    download_url = url + '/' + download_url

            logger.info(f"Found download URL: {download_url}")

            # Step 3: Follow the download button but don't download the file
            # We'll just send a HEAD request to register the click
            time.sleep(random.uniform(2, 5))  # Simulate human delay
            session.headers["Referer"] = url
            response = session.get(
                download_url,
                headers=headers,
                timeout=30,
                impersonate="firefox135",
                stream=True
            )
            response.close()

            # Log the access
            self.db.log_access(link_id, proxy, response.status_code)
            logger.info(f"Successfully accessed download link for {url}, status code: {response.status_code}")

            return True
        except Exception as e:
            logger.error(f"Error accessing {url}: {e}")
            self.db.log_access(link_id, proxy, None, str(e))
            return False

    def process_pending_accesses(self):
        """Check and process any links that need to be accessed now"""
        try:
            active_links = self.db.get_active_links()
            now = datetime.now()

            for link in active_links:
                # Only process links that haven't completed their 100 accesses for the current cycle
                if link['current_period_views'] < 100:
                    # Schedule accesses if not already done
                    # In a real application, this would be persisted in the database
                    # For simplicity, we're calculating on the fly here
                    start_date = datetime.strptime(link['current_cycle_start'], '%Y-%m-%d %H:%M:%S.%f')
                    end_date = datetime.strptime(link['current_cycle_end'], '%Y-%m-%d %H:%M:%S.%f')

                    # Calculate access times for this cycle
                    access_times = self._calculate_access_times(start_date, end_date)

                    # Find the next scheduled access (the first one that's after the current views count)
                    if link['current_period_views'] < len(access_times):
                        next_access_time = access_times[link['current_period_views']]

                        # If it's time to access the link
                        if next_access_time <= now:
                            logger.info(
                                f"Time to access link {link['url']} (ID: {link['id']}), views: {link['current_period_views']} in cycle {link['current_cycle']}")
                            self.access_link(link['id'], link['url'])

            logger.info("Completed processing pending accesses")
        except Exception as e:
            logger.error(f"Error in process_pending_accesses: {e}")