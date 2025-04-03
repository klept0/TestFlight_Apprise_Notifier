# 📄 Changelog  

## 🚀 v1.0.0 - Unreleased  

### 🎉 New Features  
- ✅ **TestFlight Monitoring** – Automatically checks TestFlight beta links for availability.  
- 🔔 **Apprise Notifications** – Sends alerts when slots become available.  
- ❤️ **Heartbeat Notifications** – Sends periodic status updates every 6 hours.  
- 📜 **Logging** – Uses Python’s `logging` module for structured logs.  
- 🛑 **Graceful Shutdown** – Handles termination signals (`SIGINT`, `SIGTERM`).  
- 🔧 **Environment Variable Validation** – Ensures all required configurations are set before execution.  

### 🛠 Setup & Configuration  
- 🐍 **Requires Python 3.8+**  
- 📦 **Dependency Installation:** `pip install -r requirements.txt`  
- ⚙️ **Uses `.env` file** for configuration (TestFlight IDs, Apprise URLs, check interval).  

### 🌐 API & Utility Functions  
- 🚀 **FastAPI Server** – Provides a simple API endpoint (`/`) for bot status.  
- 🔹 **Notification Utility** – Handles Apprise notifications with error handling.  
- 🔹 **Formatting Utility** – Helper functions for formatting dates & links.  
- 🔹 **Color-coded Console Output** – Improves log readability.  

### 🐞 Bug Fixes & Improvements  
- 🔄 **Asynchronous Task Management** – Uses `asyncio` for concurrent requests.  
- 🔄 **Improved Error Handling** – Handles network failures and missing elements gracefully.  

---

### 📢 Next Steps & Roadmap  
- ⏳ **Retry Logic for Failed Requests**  
- 📊 **Web Dashboard for Monitoring Status**  
- ⚡ **Performance Optimizations for Large ID Lists**  

---

## 📌 Release Notes  
This is the first stable release (`v1.0.0`) of **TestFlight Apprise Notifier**. Future updates will focus on enhancing performance, adding retry mechanisms, and introducing a web-based monitoring dashboard.  

📅 *Release Date: April 2, 2025*  

---

## 📥 Installation & Usage  
Refer to the [README.md](./README.md) for setup instructions and usage details.  

🚀 Happy monitoring! 🎉  
