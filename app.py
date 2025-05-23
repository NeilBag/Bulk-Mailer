import os
import csv
import smtplib
import ssl
import threading
import uuid
import sqlite3
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import (Flask, request, render_template, redirect, url_for,
                   flash, g)
from werkzeug.utils import secure_filename
from jinja2 import Template # For rendering the email template

# --- Global variables for rate limiting ---
hourly_sent_count = 0
current_hour_start_time = datetime.now() # Initialize to current time
rate_limit_lock = threading.Lock()
SMTP_HOURLY_LIMIT = 300 # Define the limit, e.g., 300

# --- Configuration ---
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS_CSV = {'csv'}
ALLOWED_EXTENSIONS_HTML = {'html', 'htm'}
DATABASE = 'instance/email_jobs.db' # Will be stored in instance folder

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['SECRET_KEY'] = os.urandom(24) # Replace with a strong, fixed secret key in production
app.config['DATABASE'] = DATABASE

# Create upload folder if it doesn't exist
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
if not os.path.exists('instance'):
    os.makedirs('instance')

# --- Database Setup ---

def get_db():
    """Connects to the specific database."""
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(
            app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
        )
        db.row_factory = sqlite3.Row # Return rows as dict-like objects
    return db

@app.teardown_appcontext
def close_connection(exception):
    """Closes the database again at the end of the request."""
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    """Initializes the database schema."""
    with app.app_context():
        db = get_db()
        with app.open_resource('schema.sql', mode='r') as f:
            db.cursor().executescript(f.read())
        db.commit()
    print("Database initialized.")

# Create schema.sql file in the same directory as app.py
# Content of schema.sql:
"""
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS failed_emails;

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_uuid TEXT UNIQUE NOT NULL,
    csv_filename TEXT NOT NULL,
    html_filename TEXT NOT NULL,
    subject TEXT NOT NULL,
    sender_email TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending', -- Pending, Running, Completed, Failed, Partial Failure
    total_emails INTEGER,
    sent_count INTEGER DEFAULT 0,
    failed_count INTEGER DEFAULT 0,
    start_time TIMESTAMP,
    end_time TIMESTAMP
);

CREATE TABLE failed_emails (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_uuid TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    error_message TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (job_uuid) REFERENCES jobs (job_uuid)
);
"""

# Initialize DB if it doesn't exist (run this once manually or check existence)
# Consider using Flask CLI command for this: `flask init-db`
# Check if DB file exists before trying to init
db_path = os.path.join(app.instance_path, os.path.basename(DATABASE))
if not os.path.exists(db_path):
    # Create the schema.sql file first!
    schema_content = """
    DROP TABLE IF EXISTS jobs;
    DROP TABLE IF EXISTS failed_emails;

    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_uuid TEXT UNIQUE NOT NULL,
        csv_filename TEXT NOT NULL,
        html_filename TEXT NOT NULL,
        subject TEXT NOT NULL,
        sender_email TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'Pending', -- Pending, Running, Completed, Failed, Partial Failure
        total_emails INTEGER,
        sent_count INTEGER DEFAULT 0,
        failed_count INTEGER DEFAULT 0,
        start_time TIMESTAMP,
        end_time TIMESTAMP
    );

    CREATE TABLE failed_emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_uuid TEXT NOT NULL,
        recipient_email TEXT NOT NULL,
        error_message TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (job_uuid) REFERENCES jobs (job_uuid)
    );
    """
    with open('schema.sql', 'w') as f:
        f.write(schema_content)
    init_db()
    os.remove('schema.sql') # Clean up schema file after use

# --- Helper Functions ---

def allowed_file(filename, allowed_extensions):
    """Checks if the file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

def send_emails_background(job_uuid, csv_path, html_path, subject, sender_email, sender_password, smtp_server, smtp_port, use_tls, use_ssl):
    """
    Function to send emails in a separate thread with delays and error handling.
    """
    global hourly_sent_count, current_hour_start_time
    # --- Configuration ---
    # Use current_app to access Flask app config within the thread context
    SEND_DELAY_SECONDS = 1.5  # Start with 1.5 seconds delay. Adjust as needed (1-5 seconds is common).
    CONNECTION_TIMEOUT = 30 # Seconds to wait for SMTP connection/commands

    # Need to create an app context to access 'g', 'current_app', etc. in the thread
        # Use the global 'app' instance defined in your script (e.g., app = Flask(__name__))
    with app.app_context():
        db = get_db() # Get DB connection within the app context
        start_time = datetime.now()
        print(f"Background job {job_uuid[:8]} started at {start_time}")

        # Update job status to Running
        try:
            db.execute("UPDATE jobs SET status = ?, start_time = ? WHERE job_uuid = ?",
                       ('Running', start_time, job_uuid))
            db.commit()
        except sqlite3.Error as e:
            print(f"Job {job_uuid[:8]}: DB Error updating status to Running: {e}")
            # Cannot proceed if DB fails here
            return

        sent_count = 0
        failed_count = 0
        total_emails = 0
        recipients = []
        initial_error = None # To store errors happening before the loop

        # 1. Read CSV and Prepare Recipient List
        try:
            with open(csv_path, mode='r', encoding='utf-8-sig') as csvfile: # utf-8-sig handles BOM
                reader = csv.DictReader(csvfile)

                # Flexible column name check (case-insensitive)
                fieldnames_lower = [name.lower() for name in reader.fieldnames or []]
                if 'firstname' not in fieldnames_lower or 'email' not in fieldnames_lower:
                     raise ValueError("CSV must contain 'FirstName' and 'Email' columns (case-insensitive).")

                # Find the actual case used in the header
                first_name_col = next((name for name in reader.fieldnames if name.lower() == 'firstname'), None)
                email_col = next((name for name in reader.fieldnames if name.lower() == 'email'), None)

                if not first_name_col or not email_col: # Should not happen if checks above passed, but safer
                    raise ValueError("Could not find 'FirstName' or 'Email' column headers.")

                for i, row in enumerate(reader):
                    email_addr = row.get(email_col, '').strip()
                    first_name = row.get(first_name_col, '').strip() # Handle missing FirstName gracefully

                    if email_addr and '@' in email_addr:
                        recipients.append({
                            'email': email_addr,
                            'first_name': first_name
                        })
                    else:
                        print(f"Job {job_uuid[:8]}: Skipping invalid email in CSV row {i+2}: {email_addr}") # +2 for header and 0-index

            total_emails = len(recipients)
            print(f"Job {job_uuid[:8]}: Found {total_emails} valid recipients in CSV.")
            db.execute("UPDATE jobs SET total_emails = ? WHERE job_uuid = ?", (total_emails, job_uuid))
            db.commit()

            if total_emails == 0:
                 initial_error = "Completed (No valid recipients found)"
                 print(f"Job {job_uuid[:8]}: {initial_error}")


        except FileNotFoundError:
            initial_error = f'Failed: CSV file not found at {csv_path}'
            print(f"Job {job_uuid[:8]}: {initial_error}")
        except ValueError as e:
             initial_error = f'Failed: Error reading CSV - {e}'
             print(f"Job {job_uuid[:8]}: {initial_error}")
        except Exception as e:
            initial_error = f'Failed: Unexpected error reading CSV - {type(e).__name__}: {e}'
            print(f"Job {job_uuid[:8]}: {initial_error}")

        # 2. Read HTML Template (only if no CSV error and recipients exist)
        email_template = None
        if not initial_error and total_emails > 0:
            try:
                with open(html_path, mode='r', encoding='utf-8') as f:
                    html_content_template = f.read()
                email_template = Template(html_content_template) # Use Jinja2 for personalization
                print(f"Job {job_uuid[:8]}: Successfully loaded HTML template.")
            except FileNotFoundError:
                initial_error = f'Failed: HTML template not found at {html_path}'
                print(f"Job {job_uuid[:8]}: {initial_error}")
            except Exception as e:
                 initial_error = f'Failed: Error reading HTML - {type(e).__name__}: {e}'
                 print(f"Job {job_uuid[:8]}: {initial_error}")

        # If there was an error reading files or no recipients, stop early
        if initial_error:
            try:
                db.execute("UPDATE jobs SET status = ?, end_time = ? WHERE job_uuid = ?",
                           (initial_error, datetime.now(), job_uuid))
                db.commit()
            except sqlite3.Error as e:
                print(f"Job {job_uuid[:8]}: DB Error updating status after file error: {e}")
            return # Stop the background task

        # 3. Connect to SMTP and Send Emails
        server = None
        connection_error = None
        try:
            print(f"Job {job_uuid[:8]}: Attempting SMTP connection to {smtp_server}:{smtp_port}...")
            context = ssl.create_default_context()
            if use_ssl:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, context=context, timeout=CONNECTION_TIMEOUT)
                print(f"Job {job_uuid[:8]}: Connected via SMTP_SSL.")
            else:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=CONNECTION_TIMEOUT)
                print(f"Job {job_uuid[:8]}: Connected via SMTP.")
                if use_tls:
                    print(f"Job {job_uuid[:8]}: Starting TLS...")
                    server.starttls(context=context)
                    print(f"Job {job_uuid[:8]}: TLS negotiation successful.")

            print(f"Job {job_uuid[:8]}: Attempting login as {sender_email}...")
            server.login(sender_email, sender_password)
            print(f"Job {job_uuid[:8]}: SMTP Login successful.")

            # --- Start Sending Loop ---
            for i, recipient in enumerate(recipients):
                # --- Start of Rate Limiting Logic ---
                waiting_for_rate_limit_reset = False
                while True: # Loop for rate limit checking and pausing
                    with rate_limit_lock: # Acquire lock for checking/updating shared variables
                        # 1. Check if the 1-hour window has passed, reset if necessary
                        if datetime.now() >= current_hour_start_time + timedelta(hours=1):
                            print(f"Job {job_uuid[:8]}: Rate limit window from {current_hour_start_time.strftime('%Y-%m-%d %H:%M:%S')} has expired. Resetting count from {hourly_sent_count}.")
                            hourly_sent_count = 0
                            current_hour_start_time = datetime.now()
                            if waiting_for_rate_limit_reset: # If we were paused due to limit
                                print(f"Job {job_uuid[:8]}: Resumed as rate limit window expired. New window started at {current_hour_start_time.strftime('%Y-%m-%d %H:%M:%S')}.")
                                try:
                                    # Ensure 'db' is accessible here, part of the app_context
                                    db.execute("UPDATE jobs SET status = ? WHERE job_uuid = ?", ('Running', job_uuid))
                                    db.commit()
                                except Exception as e_db_update:
                                    print(f"Job {job_uuid[:8]}: DB Error updating status to Running after rate limit pause: {e_db_update}")
                                waiting_for_rate_limit_reset = False # No longer waiting

                        # 2. Check if limit is reached within the current window
                        if hourly_sent_count >= SMTP_HOURLY_LIMIT:
                            if not waiting_for_rate_limit_reset: # First time hitting the limit in this pause cycle
                                resume_time_approx = current_hour_start_time + timedelta(hours=1)
                                status_msg = f'Paused - Hourly Limit ({SMTP_HOURLY_LIMIT}/hr). Resumes ~{resume_time_approx.strftime("%H:%M:%S")}'
                                print(f"Job {job_uuid[:8]}: {status_msg}")
                                try:
                                    db.execute("UPDATE jobs SET status = ? WHERE job_uuid = ?", (status_msg, job_uuid))
                                    db.commit()
                                except Exception as e_db_limit:
                                    print(f"Job {job_uuid[:8]}: DB Error updating status to Paused (limit): {e_db_limit}")
                                waiting_for_rate_limit_reset = True
                            # Lock will be released after this 'with' block for this iteration.
                        else:
                            # Limit not reached, proceed to send this email.
                            # If we were waiting and now the limit is NOT reached (e.g. window reset), ensure status is Running.
                            if waiting_for_rate_limit_reset:
                                print(f"Job {job_uuid[:8]}: Condition to send met while previously waiting for rate limit. Ensuring status is Running.")
                                try:
                                    db.execute("UPDATE jobs SET status = ? WHERE job_uuid = ?", ('Running', job_uuid))
                                    db.commit()
                                except Exception as e_db_running:
                                    print(f"Job {job_uuid[:8]}: DB Error updating status to Running (pre-send check): {e_db_running}")
                                waiting_for_rate_limit_reset = False # No longer waiting
                            break # Exit while True loop, proceed to send email for the current recipient

                    # End of 'with rate_limit_lock' for this iteration.
                    # If we are 'waiting_for_rate_limit_reset', sleep outside the lock before re-looping.
                    if waiting_for_rate_limit_reset:
                        # print(f"Job {job_uuid[:8]}: Rate limit active, sleeping for 30s before re-check.") # Optional log
                        time.sleep(30) # Sleep for 30 seconds then re-evaluate by continuing the while loop.
                                       # On next iteration, lock is re-acquired at the start of the 'with' block.
                    # If not waiting_for_rate_limit_reset, the 'break' above would have exited the 'while True' loop.
                # --- End of Rate Limiting Logic ---
                
                first_name = recipient['first_name']
                to_email = recipient['email']
                current_recipient_log = f"recipient {i+1}/{total_emails} ({to_email})"

                # Create personalized message
                msg = MIMEMultipart('alternative')
                msg['Subject'] = subject
                msg['From'] = sender_email
                msg['To'] = to_email

                try:
                    # Render HTML using Jinja template
                    personalized_html = email_template.render(
                        first_name=first_name,
                        email=to_email
                        # Add any other variables from CSV if needed
                    )
                    part_html = MIMEText(personalized_html, 'html', 'utf-8')
                    msg.attach(part_html)

                    # Send email
                    # print(f"Job {job_uuid[:8]}: Sending to {current_recipient_log}...") # Verbose
                    server.sendmail(sender_email, to_email, msg.as_string())
                    sent_count += 1
                    # Update counts immediately for better progress tracking
                    db.execute("UPDATE jobs SET sent_count = ? WHERE job_uuid = ?", (sent_count, job_uuid))
                    db.commit()
                    # print(f"Job {job_uuid[:8]}: Successfully sent to {current_recipient_log}") # Verbose

                    with rate_limit_lock: # Acquire lock to safely update shared counter
                        hourly_sent_count += 1
                        # Optional: print(f"Job {job_uuid[:8]}: Email sent. Hourly count: {hourly_sent_count}/{SMTP_HOURLY_LIMIT} in window starting {current_hour_start_time.strftime('%Y-%m-%d %H:%M:%S')}")

                    # --- DELAY ADDED HERE ---
                    # print(f"Job {job_uuid[:8]}: Pausing for {SEND_DELAY_SECONDS}s...") # Verbose
                    time.sleep(SEND_DELAY_SECONDS)
                    # --- END DELAY ---

                except smtplib.SMTPServerDisconnected:
                     # Specific handling for disconnect DURING the loop
                     failed_count += 1
                     error_msg_detail = "Server disconnected unexpectedly (mid-send). Might be rate limited or timed out."
                     error_msg_short = "SMTPServerDisconnected (mid-send)"
                     print(f"Job {job_uuid[:8]}: Failed sending to {current_recipient_log} - {error_msg_detail}")
                     # Log failure to DB
                     db.execute("INSERT INTO failed_emails (job_uuid, recipient_email, error_message) VALUES (?, ?, ?)",
                                (job_uuid, to_email, error_msg_short))
                     # Update job counts and status
                     db.execute("UPDATE jobs SET failed_count = ?, status = ? WHERE job_uuid = ?",
                                (failed_count, "Error: Server Disconnected", job_uuid))
                     db.commit()
                     print(f"Job {job_uuid[:8]}: Stopping due to mid-send disconnection.")
                     # Set connection_error flag so finally block knows connection died
                     connection_error = error_msg_detail
                     break # Exit the loop - further sends will fail on this connection

                except smtplib.SMTPException as e_send: # Catch other SMTP errors during send
                    failed_count += 1
                    error_msg = f"SMTP Error sending to {to_email}: {type(e_send).__name__} - {e_send}"
                    print(f"Job {job_uuid[:8]}: {error_msg}")
                    db.execute("INSERT INTO failed_emails (job_uuid, recipient_email, error_message) VALUES (?, ?, ?)",
                               (job_uuid, to_email, str(e_send)))
                    db.execute("UPDATE jobs SET failed_count = ? WHERE job_uuid = ?", (failed_count, job_uuid))
                    db.commit()
                    # Decide if you want to continue or break on other SMTP errors too (e.g., recipient rejected)
                    # For now, we continue to try the next recipient unless it was a disconnect.

                except Exception as e_send_general: # Catch non-SMTP errors during send (e.g., template rendering)
                    failed_count += 1
                    error_msg = f"General Error sending to {to_email}: {type(e_send_general).__name__} - {e_send_general}"
                    print(f"Job {job_uuid[:8]}: {error_msg}")
                    db.execute("INSERT INTO failed_emails (job_uuid, recipient_email, error_message) VALUES (?, ?, ?)",
                               (job_uuid, to_email, str(e_send_general)))
                    db.execute("UPDATE jobs SET failed_count = ? WHERE job_uuid = ?", (failed_count, job_uuid))
                    db.commit()
                    # Continue trying next recipient

            # --- End Sending Loop ---

        # Handle connection/login specific errors that prevent the loop from starting
        except smtplib.SMTPAuthenticationError:
            connection_error = "Failed: SMTP Authentication Error. Check email/password/app password."
            print(f"Job {job_uuid[:8]}: {connection_error}")
        except smtplib.SMTPConnectError as e:
             connection_error = f"Failed: Could not connect to SMTP server. Check server address/port. Detail: {e}"
             print(f"Job {job_uuid[:8]}: {connection_error}")
        except smtplib.SMTPServerDisconnected as e:
            # This usually happens if the server disconnects right after connect or during login/TLS
            connection_error = f"Failed: SMTP server disconnected unexpectedly during setup. Detail: {e}"
            print(f"Job {job_uuid[:8]}: {connection_error}")
        except socket.gaierror as e:
            connection_error = f"Failed: SMTP server address not found (DNS lookup failed). Check server name. Detail: {e}"
            print(f"Job {job_uuid[:8]}: {connection_error}")
        except socket.timeout as e:
             connection_error = f"Failed: Connection to SMTP server timed out ({CONNECTION_TIMEOUT}s). Detail: {e}"
             print(f"Job {job_uuid[:8]}: {connection_error}")
        except ssl.SSLError as e:
             connection_error = f"Failed: SSL Error during connection/TLS. Check port/SSL/TLS settings. Detail: {e}"
             print(f"Job {job_uuid[:8]}: {connection_error}")
        except smtplib.SMTPException as e_smtp: # Catch other SMTP setup errors
            connection_error = f"Failed: SMTP Error during setup - {type(e_smtp).__name__}: {e_smtp}"
            print(f"Job {job_uuid[:8]}: {connection_error}")
        except Exception as e_setup: # Catch any other unexpected errors during setup
            connection_error = f"Failed: Unexpected Error during setup - {type(e_setup).__name__}: {e_setup}"
            print(f"Job {job_uuid[:8]}: {connection_error}")

        finally:
            # --- Final Job Status Update ---
            end_time = datetime.now()
            final_status = None

            # If a connection/setup error occurred before sending loop
            if connection_error:
                final_status = connection_error # Use the specific error message
                failed_count = total_emails # Mark all as failed if we couldn't connect/login
                sent_count = 0
            # If sending loop finished or broke due to mid-send disconnect
            elif connection_error and connection_error.startswith("Error: Server Disconnected"):
                final_status = connection_error # Already set
                # sent_count and failed_count were updated in the loop
            else:
                # Determine status based on counts after loop completion
                if failed_count == 0 and sent_count == total_emails:
                    final_status = 'Completed'
                elif failed_count > 0 and sent_count > 0:
                    final_status = 'Partial Failure'
                elif failed_count == total_emails and sent_count == 0:
                    final_status = 'Failed: All emails' # All attempted emails failed in the loop
                elif sent_count == 0 and failed_count == 0 and total_emails > 0:
                     final_status = 'Failed: Unknown (No emails sent or failed)' # Should be rare
                elif total_emails == 0:
                    final_status = 'Completed (No recipients)' # Handled earlier, but double-check
                else:
                    final_status = 'Completed with errors (check counts)' # Fallback

            print(f"Job {job_uuid[:8]}: Finalizing. Status: {final_status}. Sent: {sent_count}, Failed: {failed_count}, Total: {total_emails}")

            try:
                # Update DB with final counts and status
                # Ensure we use the counts calculated, especially if connection_error occurred
                db.execute("UPDATE jobs SET status = ?, sent_count = ?, failed_count = ?, end_time = ? WHERE job_uuid = ?",
                           (final_status, sent_count, failed_count, end_time, job_uuid))
                db.commit()
                print(f"Job {job_uuid[:8]}: Final status updated in DB.")
            except sqlite3.Error as e:
                print(f"Job {job_uuid[:8]}: DB Error updating final status: {e}")

            # --- Cleanly close SMTP connection ---
            # Check 'server' exists and connection_error wasn't a fatal setup one preventing quit()
            if server and not connection_error:
                try:
                    print(f"Job {job_uuid[:8]}: Closing SMTP connection...")
                    server.quit()
                    print(f"Job {job_uuid[:8]}: SMTP connection closed cleanly.")
                except smtplib.SMTPServerDisconnected:
                    print(f"Job {job_uuid[:8]}: Server already disconnected before quit command.")
                except Exception as e_quit:
                     print(f"Job {job_uuid[:8]}: Error during server.quit(): {e_quit}") # Log if quit fails
            elif server and connection_error:
                # If a connection error happened mid-send, quit might still work or might fail
                print(f"Job {job_uuid[:8]}: Attempting to quit SMTP connection after error...")
                try:
                    server.quit()
                    print(f"Job {job_uuid[:8]}: SMTP connection closed after error.")
                except Exception as e_quit_err:
                    print(f"Job {job_uuid[:8]}: Error/Expected failure during server.quit() after error: {e_quit_err}")


        print(f"Background job {job_uuid[:8]} finished at {datetime.now()}")

# --- Routes ---

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        # --- Form Data and File Handling ---
        if 'csv_file' not in request.files or 'html_template' not in request.files:
            flash('Missing file part', 'danger')
            return redirect(request.url)

        csv_file = request.files['csv_file']
        html_file = request.files['html_template']
        subject = request.form.get('subject')
        sender_email = request.form.get('sender_email')
        sender_password = request.form.get('sender_password') # Handle securely!
        smtp_server = request.form.get('smtp_server')
        smtp_port_str = request.form.get('smtp_port')
        use_tls = request.form.get('use_tls') == 'true'
        use_ssl = request.form.get('use_ssl') == 'true' # SSL overrides TLS if both checked

        # Basic validation
        if csv_file.filename == '' or html_file.filename == '':
            flash('No selected file', 'warning')
            return redirect(request.url)
        if not subject or not sender_email or not sender_password or not smtp_server or not smtp_port_str:
             flash('Missing required form fields', 'danger')
             return redirect(request.url)

        try:
            smtp_port = int(smtp_port_str)
        except ValueError:
             flash('Invalid SMTP port number', 'danger')
             return redirect(request.url)

        if not allowed_file(csv_file.filename, ALLOWED_EXTENSIONS_CSV):
            flash('Invalid CSV file type', 'danger')
            return redirect(request.url)
        if not allowed_file(html_file.filename, ALLOWED_EXTENSIONS_HTML):
             flash('Invalid HTML file type', 'danger')
             return redirect(request.url)

        # Secure filenames and save uploads
        csv_filename = secure_filename(csv_file.filename)
        html_filename = secure_filename(html_file.filename)
        # Add timestamp/UUID to filenames to prevent overwrites
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        unique_csv_filename = f"{timestamp}_{uuid.uuid4().hex[:8]}_{csv_filename}"
        unique_html_filename = f"{timestamp}_{uuid.uuid4().hex[:8]}_{html_filename}"

        csv_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_csv_filename)
        html_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_html_filename)

        try:
            csv_file.save(csv_path)
            html_file.save(html_path)
        except Exception as e:
             flash(f'Error saving files: {e}', 'danger')
             print(f"Error saving files: {e}")
             return redirect(request.url)

        # --- Create Job Record in DB ---
        job_uuid = str(uuid.uuid4())
        db = get_db()
        try:
            db.execute(
                "INSERT INTO jobs (job_uuid, csv_filename, html_filename, subject, sender_email, status) VALUES (?, ?, ?, ?, ?, ?)",
                (job_uuid, unique_csv_filename, unique_html_filename, subject, sender_email, 'Pending')
            )
            db.commit()
        except sqlite3.Error as e:
             flash(f'Database error creating job: {e}', 'danger')
             print(f"Database error creating job: {e}")
             # Clean up saved files if DB fails
             if os.path.exists(csv_path): os.remove(csv_path)
             if os.path.exists(html_path): os.remove(html_path)
             return redirect(request.url)


        # --- Start Background Thread ---
        # Pass all necessary data to the thread function
        thread = threading.Thread(target=send_emails_background, args=(
            job_uuid,
            csv_path,
            html_path,
            subject,
            sender_email,
            sender_password, # Pass the password securely (though still in memory)
            smtp_server,
            smtp_port,
            use_tls,
            use_ssl
        ))
        thread.daemon = True # Allows app to exit even if threads are running (use with caution)
        thread.start()

        flash(f'Email sending job started (Job ID: {job_uuid[:8]}...). Check the dashboard for status.', 'success')
        return redirect(url_for('dashboard'))

    # --- GET Request ---
    return render_template('index.html')


@app.route('/dashboard')
def dashboard():
    db = get_db()
    try:
        jobs_cursor = db.execute("SELECT * FROM jobs ORDER BY start_time DESC, id DESC")
        jobs = jobs_cursor.fetchall()
    except sqlite3.Error as e:
        flash(f"Error fetching dashboard data: {e}", "danger")
        print(f"Error fetching dashboard data: {e}")
        jobs = []
    return render_template('dashboard.html', jobs=jobs)

@app.route('/dashboard/failures/<job_uuid>')
def job_failures(job_uuid):
     # --- Optional: Route to view specific failures for a job ---
     db = get_db()
     try:
        job_cursor = db.execute("SELECT * FROM jobs WHERE job_uuid = ?", (job_uuid,))
        job = job_cursor.fetchone()
        if not job:
             flash("Job not found.", "warning")
             return redirect(url_for('dashboard'))

        failures_cursor = db.execute(
            "SELECT recipient_email, error_message, timestamp FROM failed_emails WHERE job_uuid = ? ORDER BY timestamp DESC",
            (job_uuid,)
        )
        failures = failures_cursor.fetchall()

     except sqlite3.Error as e:
         flash(f"Error fetching failure data: {e}", "danger")
         print(f"Error fetching failure data: {e}")
         return redirect(url_for('dashboard'))

     # You would create a new template 'job_failures.html' to display this data
     # return render_template('job_failures.html', job=job, failures=failures)
     # For now, just show a basic message or redirect
     flash(f"Displaying failures for Job {job_uuid[:8]}... (Implementation for display page needed)", "info")
     # Example: return basic info for now
     failure_info = "<br>".join([f"{f['timestamp']}: {f['recipient_email']} - {f['error_message']}" for f in failures])
     return f"<h1>Failures for Job {job['job_uuid'][:8]}</h1><p>Subject: {job['subject']}</p><p>Total Failures: {job['failed_count']}</p><hr><p>{failure_info}</p><a href='{url_for('dashboard')}'>Back to Dashboard</a>"


# --- Main Execution ---
if __name__ == '__main__':
    # Make sure the instance folder exists where the DB will be stored
    if not os.path.exists(app.instance_path):
        os.makedirs(app.instance_path)

    # You might want to add a Flask CLI command to initialize the DB cleanly
    # Example: flask --app app init-db
    # @app.cli.command('init-db')
    # def init_db_command():
    #     """Clear existing data and create new tables."""
    #     init_db()
    #     click.echo('Initialized the database.')

    app.run(debug=True) # Turn off debug mode in production!
