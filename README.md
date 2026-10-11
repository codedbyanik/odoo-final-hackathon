# 🏙️ Skyline Club

**A Campus Club & Student Organization Management System**

Skyline Club is a web application built with Flask and SQLite to simplify campus club management. It brings events, memberships, ticketing, merchandise, finances, and committee administration together in one place.

## ✨ Features

- 🔐 **Authentication** — Separate login flows for members and committee administrators.
- 👥 **Membership Management** — Add members, track membership status, record payments, and send reminders.
- 📅 **Event Management** — Publish and manage club events and ticket information.
- 🎟️ **Ticketing** — Manage ticket-related information for club events.
- 🛍️ **Merchandise Shop** — Display products with images, descriptions, and prices.
- 📦 **Order Management** — Collect customer details and manage merchandise orders.
- 📊 **Admin Dashboard** — View club summaries, upcoming events, membership data, and charts.
- 💰 **Finance Management** — Record and review club income and expenses.
- 📑 **Excel Reports** — Export supported administrative data into Excel reports.
- 📢 **Announcements** — Share important club updates.
- 🎯 **Tasks & Fundraisers** — Coordinate club activities and fundraising records.
- 🔎 **Search & Filtering** — Find records on supported management pages.
- 🌙 **Dark Mode** — Switch between supported interface themes.
- 📱 **Responsive UI** — A modern interface for desktop and mobile screens.

*Feature availability depends on the current application configuration.*

## 🛠️ Tech Stack

| Component | Technology |
|---|---|
| Backend | Python, Flask |
| Database | SQLite |
| Frontend | HTML, CSS, JavaScript |
| Templates | Jinja2 |
| Charts | Chart.js, where configured |
| Security | CSRF protection and password hashing where implemented |
| Reporting | Excel export functionality |
| IDE | Visual Studio Code |

## 📁 Project Structure

```text
skyline-club/
├── app.py                  # Flask application and routes
├── db.py                   # Database schema and helper functions
├── club.db                 # Local SQLite database
├── templates/              # Jinja2 HTML templates
│   ├── base.html
│   └── ...
├── static/
│   ├── style.css           # Shared styling
│   └── script.js           # Client-side interactions
├── uploads/                # Uploaded files, if configured
├── .env                    # Local configuration (never commit)
├── .env.example            # Example environment variables
└── README.md
```

*The exact folders and files may vary depending on the current project configuration.*

## ⚙️ Installation & Setup

### 1️⃣ Clone or Download the Project

Open the project folder in Visual Studio Code.

### 2️⃣ Create a Virtual Environment

For Windows PowerShell:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

For Command Prompt:

```bat
.venv\Scripts\activate.bat
```

### 3️⃣ Install Dependencies

If `requirements.txt` exists:

```bash
pip install -r requirements.txt
```

Otherwise, install the packages required by your application. For example:

```bash
pip install Flask Flask-WTF python-dotenv
```

Install any additional libraries imported by `app.py`.

### 4️⃣ Configure Environment Variables

If `.env.example` is provided, copy it to `.env` and configure the variables required by the application, such as:

- Flask secret key
- Administrator credentials
- Email service credentials, if enabled
- Payment provider credentials, if enabled

⚠️ **Never commit real secrets or API keys to GitHub.**

### 5️⃣ Initialize the Database

Skyline Club uses SQLite for local data storage. Follow the initialization process implemented in `db.py` and `app.py`.

If you already have a `club.db`, back it up before making database changes.

### 6️⃣ Run the Application

```bash
python app.py
```

Open the local address shown in your terminal, commonly:

`http://127.0.0.1:5000`

## 🗄️ Database

SQLite stores application data locally. Depending on the current schema, this may include:

- Member accounts and membership tiers
- Events and tickets
- Merchandise products and orders
- Financial records
- Committee accounts
- Announcements and club activities

## 🔒 Security

- Keep `.env`, passwords, API keys, and private data out of public repositories.
- Rotate any secret that has been exposed.
- Preserve CSRF protection on protected forms.
- Protect administrator routes with appropriate access checks.
- Ensure private ticket and order pages verify ownership.
- Back up the database before destructive changes.
- Configure HTTPS and production security settings before public deployment.

## 🧑‍💻 Development

| File or Folder | Purpose |
|---|---|
| `app.py` | Routes and application logic |
| `db.py` | Database schema and queries |
| `templates/` | HTML templates |
| `static/style.css` | Styling and responsive layouts |
| `static/script.js` | Browser-side interactions |

After making changes, test login, navigation, forms, uploads, order handling, database operations, and report exports.

## 🐛 Troubleshooting

**CSS changes are not visible**
- Save the stylesheet.
- Confirm `base.html` loads the correct CSS file.
- Press `Ctrl + Shift + R` to hard-refresh the browser.

**Template BuildError**
- Check that the endpoint in `url_for()` matches an existing Flask route.

**Database errors**
- Verify the database path and initialization process.
- Back up existing data before changing the schema.

**Image upload errors**
- Check the upload directory, allowed extensions, file-size limit, and permissions.

**Login or email errors**
- Verify required environment variables and service credentials without exposing secrets.

## 🚀 Project Status

Skyline Club is a student-organization management project designed to support campus club activities and administration.

Before deploying publicly, test authentication, access control, ticket ownership, order tracking, uploads, database operations, and report exports.

## 📄 License

Add the license selected by the project owner before distributing or reusing this software.

---

**Built to bring campus communities together.** 🎓
