# 🚀 TestFlight Apprise Notifier (Dev Branch)  

📡 **Monitors TestFlight beta links, provides real-time updates, and allows dynamic ID management via a Web Dashboard!**  
Uses **FastAPI** for the backend, **React** for the frontend, **WebSockets** for real-time updates, and **Apprise** for notifications.  

---

## ✨ New Features (v1.0.1)  

🌐 **WebSockets Support** – Live updates on TestFlight status.  
🖥️ **React Dashboard** – Add/remove TestFlight IDs dynamically.  
📡 **Live API Endpoints** – No more editing `.env`; use the UI instead!  
🛠 **Optimized Backend** – Faster and more efficient API.  

---

## 📦 Setup  

### **🔧 Prerequisites**  

- 🐍 **Python 3.8+**  
- ⚛️ **Node.js 16+** (for the frontend)  
- 📦 Install dependencies:  

  ```bash
  # Backend
  pip install -r requirements.txt  

  # Frontend
  cd frontend  
  npm install  
  ```

### **⚙️ Configuration**  

Instead of using `.env`, IDs are now stored in `testflight_ids.json`:  

```json
{
  "ids": ["abc123", "def456", "ghi789"]
}
```

---

## 🚀 Running the Application  

1️⃣ **Start the Backend:**  
```bash
python main.py
```
Backend API available at **[http://localhost:8089](http://localhost:8089)**  

2️⃣ **Start the Frontend:**  
```bash
cd frontend  
npm start  
```
Dashboard available at **[http://localhost:3000](http://localhost:3000)**  

---

## 🛠 Key Components  

📡 **Backend (FastAPI)**  
- Manages TestFlight checks.  
- Provides API for adding/removing IDs.  
- Sends WebSocket updates to the frontend.  

⚛️ **Frontend (React + WebSockets)**  
- Displays real-time TestFlight status.  
- Allows adding/removing IDs dynamically.  

---

## 📜 Logging  

Example output:  

```plaintext
2025-04-02 12:00:00 - INFO - WebSocket update: abc123 - Available  
2025-04-02 12:01:00 - INFO - ID added via API: xyz987  
```

---

## 🛑 Graceful Shutdown  

- Cleans up WebSocket connections.  
- Closes any open API requests.  

---

## 🤝 Contributing  

🚀 **Want to help improve this project?** Feel free to **submit issues** or **create a pull request**!  

---

## 📜 License  

This project is licensed under the **MIT License** 