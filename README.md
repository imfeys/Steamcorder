# Steamcorder - Automatically upload Steam Screenshots to Discord

Steamcorder is a **Python-based GUI application** that automatically uploads your Steam screenshots to a **Discord server** via a webhook.
---

## 📌 Features

✅ **Automatic Upload**: Detects new Steam screenshots and uploads them to a Discord webhook.  
✅ **Customizable Delay**: Set a delay before uploading to ensure files are fully saved.  
✅ **File Filtering**: Supports popular image formats: `.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`.  
✅ **Minimize to Tray**: Keeps the app running in the background.  
✅ **Startup Option**: Can start with Windows.  
✅ **Easy-to-Use GUI**: Built with **PyQt6** for a smooth user experience.  

TO DO LIST CURRENTLY:
redesign GUI,
New logo,
exe file

---

## 📦 Installation

### 1️⃣ Prerequisites
Ensure you have **Python 3.8+** installed. You can download it from [Python.org](https://www.python.org/downloads/).

### 2️⃣ Install Dependencies
```sh
pip install -r requirements.txt
```

### 3️⃣ Run the App
```sh
python steamcorder.py
```

###   Compile exe yourself
```sh
pyinstaller --noconsole --onefile --icon=icon.ico --add-data "icon.ico;." --add-data "icon.png;." --add-data "discord_icon.png;." Steamcorder.py

```

---

## 🔧 Setup & Usage

### 📂 Step 1: Select Your Steam Screenshot Folder
- Go to **Steam > Settings > In-Game**.
- Enable **"Save an uncompressed copy of screenshots"**.
- Choose a folder to save screenshots.
- Select this folder in **Steamcorder**.

### 🔗 Step 2: Add a Discord Webhook
- Open Discord, go to **Server Settings > Integrations > Webhooks**.
- Create a new webhook and copy the **Webhook URL**.
- Paste it into **Steamcorder**.

### ▶ Step 3: Start Monitoring
- Click **Start Monitoring** to begin automatic uploads.
- Screenshots will be detected and uploaded instantly.

### ⚙ Step 4: Adjust Settings (Optional)
- **Upload Delay**: Prevents partial file uploads.
- **Minimize to Tray**: Keeps the app running in the background.
- **Start on Windows Boot**: Auto-starts Steamcorder with Windows.

---

## 🎨 Screenshot
![image](https://github.com/user-attachments/assets/d86a3543-a2e1-46ca-8cfb-f625ce9b0498)
![image](https://github.com/user-attachments/assets/4ce27699-d8de-429a-88b9-0de5a0a5a346)




---

## 🔥 Advanced Features
- **Hidden Webhook URL**: Option to hide the webhook field for security.

---

## 🛠 Tech Stack
- **Python** (3.8+)
- **PyQt6** (GUI)
- **Watchdog** (File monitoring)
- **Requests** (Webhook API integration)

---

## 🚀 Contributing
Contributions are welcome! Feel free to **fork** this repo and submit a **pull request**.

---

## 📜 License
This project is licensed under the **MIT License**. Feel free to use and modify it.

---

## 💬 Support
- **Discord**: [Join our server](https://discord.gg/dMvCH93sYX)
- **Issues**: Report bugs on [GitHub Issues](https://github.com/your-repo/issues)

---

Happy Screenshot Sharing! 🎮📤

