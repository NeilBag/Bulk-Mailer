# Flask Bulk Emailer
Flask Bulk Emailer is a Python-based web application designed to simplify sending personalized bulk emails. Users can upload a CSV file containing a list of recipients (with first names and email addresses) and an HTML template for the email body. The application then uses SMTP (Simple Mail Transfer Protocol) to dispatch these emails. It features a dashboard for tracking the status of email sending jobs and provides details on any failures, making it easier to manage bulk email campaigns directly from your web browser.

## Key Features

*   **CSV Upload:** Easily upload a CSV file containing your recipient list. Requires 'FirstName' and 'Email' columns (case-insensitive) for personalization.
*   **HTML Email Templates:** Use your own HTML files as email templates.
*   **Jinja2 Templating:** Personalize emails using Jinja2 syntax (e.g., `{{ first_name }}`, `{{ email }}`) within your HTML template, referencing columns from your CSV.
*   **SMTP Configuration:** Configure SMTP server settings including server address, port, sender credentials, and connection security (TLS/SSL).
*   **Background Sending:** Emails are sent in a background thread, allowing you to continue using the application or close your browser.
*   **Job Dashboard:** Monitor the status of all email sending jobs (e.g., Pending, Running, Completed, Failed, Partial Failure).
*   **Progress Tracking:** View counts for total emails, successfully sent emails, and failed emails for each job.
*   **Detailed Error Reporting:** For jobs with failures, view a list of recipients who were not reached and the specific error messages.
*   **Automatic Database Setup:** SQLite database is automatically initialized on first run to store job information.
*   **Responsive UI:** Basic responsive design using Bootstrap for usability on different screen sizes.

## Tech Stack

*   **Backend:** Python, Flask
*   **Templating:** Jinja2 (for HTML email templates and web pages)
*   **Database:** SQLite (via `sqlite3` module)
*   **Frontend:** HTML, CSS (Bootstrap for basic styling)
*   **Standard Libraries:** `smtplib` (for sending emails), `csv`, `os`, `threading`, `uuid`, `datetime`

## Prerequisites

*   **Python:** Version 3.7 or higher.
*   **pip:** Python package installer (usually comes with Python).
*   **Virtual Environment (Recommended):** Familiarity with creating and activating Python virtual environments (e.g., using `venv`) is recommended to keep dependencies isolated.
*   **Web Browser:** A modern web browser to access the application (e.g., Chrome, Firefox, Edge).

## Setup and Installation

1.  **Clone the Repository:**
    ```bash
    git clone [https://github.com/your-username/your-repository-name.git](https://github.com/NeilBag/Bulk-Mailer.gi](https://github.com/NeilBag/Bulk-Mailer.git)
    cd your-repository-name
    ```
    *(Replace `https://github.com/your-username/your-repository-name.git` with the actual URL of this repository if you know it, otherwise leave as a placeholder).*

2.  **Create and Activate a Virtual Environment (Recommended):**
    *   On macOS and Linux:
        ```bash
        python3 -m venv venv
        source venv/bin/activate
        ```
    *   On Windows:
        ```bash
        python -m venv venv
        .\venv\Scripts\activate
        ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Database Initialization:**
    The application uses SQLite. The database file (`instance/email_jobs.db`) and its necessary tables are automatically created in the `instance` folder when you first run the application if they don't already exist. No manual database setup is required.

## Running the Application

1.  **Ensure your virtual environment is activated** (if you created one).

2.  **Start the Flask Development Server:**
    Open your terminal in the project's root directory and run:
    ```bash
    flask run
    ```
    Alternatively, you can run the `app.py` script directly:
    ```bash
    python app.py
    ```

3.  **Access the Application:**
    Open your web browser and navigate to:
    [http://127.0.0.1:5000/](http://127.0.0.1:5000/)

    The application runs with `debug=True` by default as configured in `app.py`. For production deployment, you should turn this off and use a production-ready WSGI server (e.g., Gunicorn, Waitress).

## Usage Instructions

The application provides a web interface to upload your recipient list and email template, configure SMTP settings, and send emails.

### 1. Home Page (`/`) - Sending Emails

When you first access the application, you'll see a form with the following fields:

*   **CSV File:**
    *   Upload your recipient list here.
    *   The CSV file **must** contain columns named `FirstName` and `Email`. Column names are case-insensitive (e.g., `firstname`, `email` also work).
    *   Other columns in the CSV will be ignored.
    *   Example CSV structure:
        ```csv
        FirstName,Email,OtherColumn
        Alice,alice@example.com,Data1
        Bob,bob@example.org,Data2
        ```

*   **HTML Email Template:**
    *   Upload your email's HTML content here.
    *   You can personalize the email using Jinja2 templating syntax by inserting placeholders that correspond to your CSV column headers. The available placeholders are:
        *   `{{ first_name }}`: Replaced with the value from the 'FirstName' column.
        *   `{{ email }}`: Replaced with the value from the 'Email' column.
    *   Example HTML template snippet:
        ```html
        <p>Hi {{ first_name }},</p>
        <p>This is a personalized email for {{ email }}.</p>
        ```

*   **Email Subject:**
    *   Enter the subject line for your bulk email campaign.

*   **Sender SMTP Credentials:**
    *   **Sender Email Address:** The email address from which the emails will be sent.
    *   **Sender Password / App Password:** The password for the sender's email account.
        *   **Important:** For services like Gmail that use 2-Factor Authentication (2FA), you'll likely need to generate and use an "App Password". Using your regular account password may not work.
    *   **SMTP Server Address:** The address of your email provider's SMTP server (e.g., `smtp.gmail.com`, `smtp.office365.com`).
    *   **SMTP Port:** The port number for the SMTP server (e.g., `587` for TLS, `465` for SSL).
    *   **Use TLS / Use SSL:** Checkboxes to enable transport layer security.
        *   **TLS (Transport Layer Security):** Typically used with port 587. This is generally recommended.
        *   **SSL (Secure Sockets Layer):** Typically used with port 465. If SSL is checked, it will be used even if TLS is also checked.

*   **Submit:**
    *   Once all fields are correctly filled, click the "Start Sending Emails" button.
    *   The application will then start processing your request in the background. You will be redirected to the Dashboard.

### 2. Dashboard Page (`/dashboard`)

*   This page displays a history of all email sending jobs and their current status.
*   You can refresh this page to see updates on ongoing jobs.
*   Key information displayed for each job:
    *   **Job ID:** A unique identifier for the job (first 8 characters).
    *   **Status:** Current state of the job (e.g., `Pending`, `Running`, `Completed`, `Failed`, `Partial Failure`, `Error: ...`). Statuses are color-coded for easy identification.
    *   **Subject:** The email subject for the job.
    *   **CSV File:** The name of the uploaded CSV file.
    *   **Template File:** The name of the uploaded HTML template file.
    *   **Total:** Total number of valid recipients found in the CSV.
    *   **Sent:** Number of emails successfully handed off to the SMTP server.
    *   **Failed:** Number of emails that failed to send.
    *   **Started:** Timestamp when the job began processing.
    *   **Finished:** Timestamp when the job completed or was stopped due to a fatal error.
    *   **Details:** If a job has `Failed > 0`, a "View Failures" button will appear.

### 3. Job Failures Page (`/dashboard/failures/<job_uuid>`)

*   Accessible by clicking the "View Failures" button on the dashboard for a specific job.
*   This page lists each recipient for whom the email sending failed, along with:
    *   **Recipient Email:** The email address that could not be reached.
    *   **Error Message:** The specific error returned by the SMTP server or the application.
    *   **Timestamp:** When the failure was recorded.

## Important Notes/Considerations

*   **Security:**
    *   **Sender Password:** Be extremely cautious with your email account password. It is highly recommended to use an **App Password** if your email provider (like Gmail or Outlook) supports it, especially if you have 2-Factor Authentication (2FA) enabled. Using your main account password directly in applications can be a security risk.
    *   The password entered into the form is used for the SMTP connection and is not stored persistently by the application in plain text after job submission (it's held in memory during the sending process).
    *   This application is designed for ease of use. For production environments with high-security needs, consider more robust secret management solutions.

*   **Rate Limiting & Sending Delays:**
    *   The application includes a `SEND_DELAY_SECONDS` setting (currently hardcoded in `app.py` to `1.5` seconds) between sending individual emails. This is crucial to avoid being rate-limited or flagged as spam by your email provider.
    *   If you are sending a very large number of emails or encounter issues, you might need to adjust this delay in the `app.py` code.
    *   Always respect your email provider's terms of service regarding bulk emailing.

*   **Email Delivery vs. Sending:**
    *   The dashboard shows emails as "Sent" when the application successfully hands them off to your SMTP server. This **does not guarantee delivery** to the recipient's inbox.
    *   Actual delivery depends on many factors, including recipient server status, spam filters, email content, sender reputation, etc. This application does not track bounces, opens, or clicks.

*   **File Storage:**
    *   Uploaded CSV and HTML files are stored in the `uploads/` directory. Each filename is prefixed with a timestamp and a unique ID to prevent overwrites.
    *   Consider cleaning out this folder periodically if you process many jobs and disk space is a concern.

*   **Database:**
    *   Job information and failure logs are stored in an SQLite database file located at `instance/email_jobs.db`.
    *   If you need to back up your job history, ensure you copy this file.

*   **Error Handling:**
    *   The application attempts to catch common errors related to file uploads, SMTP connections, and individual email sending.
    *   Always check the "Status" on the dashboard and the "View Failures" page for details if a job doesn't complete as expected.

*   **Development vs. Production:**
    *   The application is set to run in `debug=True` mode by default, which is helpful for development but **should be disabled for production**.
    *   For production use, deploy using a proper WSGI server like Gunicorn or Waitress.

## File Structure Overview

```
.
├── app.py              # Main Flask application file, contains all routes and logic.
├── requirements.txt    # Python package dependencies.
├── LICENSE             # Project license file (GNU General Public License v3).
├── README.md           # This file.
├── instance/           # Created automatically, stores instance-specific data.
│   └── email_jobs.db   # SQLite database for job tracking (created automatically).
├── static/             # Static assets (CSS, JavaScript, images).
│   └── style.css       # Basic custom stylesheets.
├── templates/          # HTML templates used by Flask.
│   ├── base.html       # Base template providing common layout.
│   ├── dashboard.html  # Template for the job dashboard page.
│   └── index.html      # Template for the main email sending form page.
└── uploads/            # Created automatically, stores uploaded CSV and HTML files.
```
