import tkinter as tk
from tkinter import messagebox
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import threading
import time
from datetime import datetime, timedelta
import os
import json  # 新增：用於處理儲存檔案

# 設定檔名稱
CONFIG_FILE = "user_config.json"

class NueipChromeClickerV4:
    def __init__(self, root):
        self.root = root
        self.root.title("NUEIP 自動打卡神器 v4 (記憶帳密版)")
        self.root.geometry("600x720") 
        self.driver = None
        self.is_running = False

        # --- UI 標題 ---
        tk.Label(root, text="NUEIP 自動登入 & 下班", font=("Arial", 16, "bold"), fg="#1a73e8").pack(pady=10)

        # 1. 帳密設定區
        frame_auth = tk.LabelFrame(root, text="第一步：輸入登入資訊 (自動記憶)", padx=10, pady=10, fg="blue")
        frame_auth.pack(padx=10, fill="x")

        tk.Label(frame_auth, text="公司代碼:").grid(row=0, column=0, sticky="w")
        self.entry_company = tk.Entry(frame_auth, width=30)
        self.entry_company.grid(row=0, column=1, pady=2, sticky="w")

        tk.Label(frame_auth, text="員工編號:").grid(row=1, column=0, sticky="w")
        self.entry_user = tk.Entry(frame_auth, width=30)
        self.entry_user.grid(row=1, column=1, pady=2, sticky="w")

        tk.Label(frame_auth, text="登入密碼:").grid(row=2, column=0, sticky="w")
        self.entry_pass = tk.Entry(frame_auth, width=30, show="*") 
        self.entry_pass.grid(row=2, column=1, pady=2, sticky="w")

        # 2. 時間與網址設定
        frame_input = tk.LabelFrame(root, text="第二步：設定時間與網址", padx=10, pady=10)
        frame_input.pack(padx=10, pady=10, fill="x")

        tk.Label(frame_input, text="登入網址:").grid(row=0, column=0, sticky="w")
        self.entry_url = tk.Entry(frame_input, width=50)
        self.entry_url.insert(0, "https://cloud.nueip.com/login")
        self.entry_url.grid(row=0, column=1, pady=5)

        tk.Label(frame_input, text="設定時間 (HH:MM):").grid(row=1, column=0, sticky="w")
        self.entry_time = tk.Entry(frame_input, width=50)
        self.entry_time.insert(0, "18:00")
        self.entry_time.grid(row=1, column=1, pady=5)
        
        # 測試模式開關
        self.test_mode_var = tk.BooleanVar(value=True) 
        self.chk_test_mode = tk.Checkbutton(frame_input, text="開啟測試模式 (只標記不點擊)", 
                                            variable=self.test_mode_var, fg="red", font=("Arial", 10, "bold"))
        self.chk_test_mode.grid(row=2, column=1, sticky="w", pady=5)

        # 3. 進階 XPath (已更新為無敵版預設值)
        frame_xpath = tk.LabelFrame(root, text="進階定位 (預設已優化)", padx=10, pady=10)
        frame_xpath.pack(padx=10, fill="x")
        
        tk.Label(frame_xpath, text="代碼輸入框:").grid(row=0, column=0, sticky="w")
        self.xp_company = tk.Entry(frame_xpath, width=40)
        self.xp_company.insert(0, "//input[@placeholder='公司代碼']")
        self.xp_company.grid(row=0, column=1)

        tk.Label(frame_xpath, text="帳號輸入框:").grid(row=1, column=0, sticky="w")
        self.xp_user = tk.Entry(frame_xpath, width=40)
        self.xp_user.insert(0, "//input[@placeholder='員工編號' or @name='dept_input']") 
        self.xp_user.grid(row=1, column=1)

        tk.Label(frame_xpath, text="密碼輸入框:").grid(row=2, column=0, sticky="w")
        self.xp_pass = tk.Entry(frame_xpath, width=40)
        self.xp_pass.insert(0, "//input[@type='password']")
        self.xp_pass.grid(row=2, column=1)

        tk.Label(frame_xpath, text="登入按鈕:").grid(row=3, column=0, sticky="w")
        self.xp_login_btn = tk.Entry(frame_xpath, width=40)
        self.xp_login_btn.insert(0, "//button[contains(., '登入') or @type='submit']")
        self.xp_login_btn.grid(row=3, column=1)
        
        tk.Label(frame_xpath, text="下班按鈕:").grid(row=4, column=0, sticky="w")
        self.xp_clockout = tk.Entry(frame_xpath, width=40)
        # 【重要更新】這裡直接換成無敵版定位
        self.xp_clockout.insert(0, "//div[contains(., '打卡鐘')]//*[contains(text(), '下班')]")
        self.xp_clockout.grid(row=4, column=1)

        # 4. 執行控制
        frame_control = tk.Frame(root, pady=10)
        frame_control.pack(fill="x", padx=20)

        self.status_label = tk.Label(root, text="狀態：準備就緒", fg="gray", font=("Arial", 12))
        self.status_label.pack(pady=5)

        self.btn_start = tk.Button(frame_control, text="儲存並啟動排程", 
                                   command=self.start_schedule, bg="#e8f0fe", font=("Arial", 12), height=2)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=5)

        self.btn_cancel = tk.Button(frame_control, text="取消/停止", 
                                    command=self.cancel_schedule, bg="#ffcdd2", fg="red", font=("Arial", 12), state="disabled", height=2)
        self.btn_cancel.pack(side="right", fill="x", expand=True, padx=5)

        # 程式啟動時，嘗試讀取紀錄
        self.load_config()

    def load_config(self):
        """讀取 JSON 設定檔並填入欄位"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # 如果有資料，清空欄位後填入
                    if "company" in data:
                        self.entry_company.delete(0, tk.END)
                        self.entry_company.insert(0, data["company"])
                    if "user" in data:
                        self.entry_user.delete(0, tk.END)
                        self.entry_user.insert(0, data["user"])
                    if "password" in data:
                        self.entry_pass.delete(0, tk.END)
                        self.entry_pass.insert(0, data["password"])
                    if "url" in data:
                        self.entry_url.delete(0, tk.END)
                        self.entry_url.insert(0, data["url"])
                    # 如果有儲存時間，也可以考慮讀取，但時間通常每次不一樣，這裡先不讀取時間
            except Exception as e:
                print(f"讀取設定檔失敗: {e}")

    def save_config(self):
        """將目前欄位內容存入 JSON"""
        data = {
            "company": self.entry_company.get(),
            "user": self.entry_user.get(),
            "password": self.entry_pass.get(),
            "url": self.entry_url.get()
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"儲存設定檔失敗: {e}")

    def start_schedule(self):
        # 1. 先儲存設定
        self.save_config()

        # 簡單檢查
        if not self.entry_company.get() or not self.entry_user.get() or not self.entry_pass.get():
            messagebox.showwarning("提醒", "請先輸入公司代碼、帳號與密碼！")
            return

        target_time_str = self.entry_time.get()
        try:
            target_dt = datetime.strptime(target_time_str, "%H:%M")
            now = datetime.now()
            target_dt = target_dt.replace(year=now.year, month=now.month, day=now.day)
            
            trigger_dt = target_dt + timedelta(minutes=1)
            self.trigger_time_str = trigger_dt.strftime("%H:%M:%S")
            
            self.is_running = True
            mode_text = "【測試模式】" if self.test_mode_var.get() else "【正式模式】"
            self.status_label.config(text=f"{mode_text} 等待於 {self.trigger_time_str} 執行...", fg="blue")
            
            self.btn_start.config(state="disabled")
            self.btn_cancel.config(state="normal")
            
            threading.Thread(target=self.wait_loop, args=(trigger_dt,), daemon=True).start()

        except ValueError:
            messagebox.showerror("錯誤", "時間格式錯誤")

    def cancel_schedule(self):
        if self.is_running:
            self.is_running = False
            self.status_label.config(text="狀態：使用者已取消排程", fg="red")
            messagebox.showinfo("取消", "已停止任務。")
            self.reset_buttons()

    def reset_buttons(self):
        self.btn_start.config(state="normal", text="儲存並啟動排程")
        self.btn_cancel.config(state="disabled")

    def wait_loop(self, trigger_dt):
        while self.is_running:
            now = datetime.now()
            remaining = (trigger_dt - now).total_seconds()

            if remaining <= 0:
                if self.is_running:
                    self.run_automation()
                break
            
            if int(remaining) % 1 == 0:
                self.root.after(0, lambda: self.status_label.config(text=f"倒數: {int(remaining)} 秒"))
            time.sleep(0.5)

    def run_automation(self):
        if not self.is_running: return

        self.root.after(0, lambda: self.status_label.config(text="啟動 Chrome...", fg="orange"))
        
        url = self.entry_url.get()
        company = self.entry_company.get()
        user = self.entry_user.get()
        pwd = self.entry_pass.get()
        
        # 取得 XPaths (使用預設優化過的值)
        xp_company = self.xp_company.get()
        xp_user = self.xp_user.get()
        xp_pass = self.xp_pass.get()
        xp_login = self.xp_login_btn.get()
        xp_clockout = self.xp_clockout.get()
        
        is_test_mode = self.test_mode_var.get()

        try:
            options = Options()
            options.add_experimental_option("detach", True)
            options.add_argument("--start-maximized")
            
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=options)
            self.driver = driver

            # 1. 進入登入頁
            if not self.is_running: return
            driver.get(url)
            wait = WebDriverWait(driver, 15)
            
            self.root.after(0, lambda: self.status_label.config(text="正在輸入帳號密碼..."))

            # 2. 自動填入帳密
            elem_company = wait.until(EC.visibility_of_element_located((By.XPATH, xp_company)))
            elem_company.clear()
            elem_company.send_keys(company)
            
            elem_user = driver.find_element(By.XPATH, xp_user)
            elem_user.clear()
            elem_user.send_keys(user)
            
            elem_pass = driver.find_element(By.XPATH, xp_pass)
            elem_pass.clear()
            elem_pass.send_keys(pwd)
            
            time.sleep(0.5)

            # 3. 點擊登入
            btn_login = driver.find_element(By.XPATH, xp_login)
            btn_login.click()
            
            self.root.after(0, lambda: self.status_label.config(text="登入中，等待頁面跳轉..."))

            # 4. 等待下班按鈕
            if not self.is_running: return
            clockout_btn = wait.until(EC.element_to_be_clickable((By.XPATH, xp_clockout)))
            driver.execute_script("arguments[0].scrollIntoView();", clockout_btn)
            time.sleep(1)

            # ================= 關鍵區域 =================
            if not self.is_running: 
                self.root.after(0, lambda: messagebox.showwarning("取消", "已取消，未執行打卡！"))
                return

            if is_test_mode:
                # 【測試模式】
                driver.execute_script("arguments[0].style.border='5px solid red';", clockout_btn)
                driver.execute_script("arguments[0].style.backgroundColor='yellow';", clockout_btn)
                self.root.after(0, lambda: messagebox.showinfo("測試成功", "自動登入成功！\n已找到下班按鈕 (未點擊)"))
                self.root.after(0, lambda: self.status_label.config(text="測試完成 (未打卡)", fg="green"))
            else:
                # 【正式模式】
                clockout_btn.click() 
                self.root.after(0, lambda: messagebox.showinfo("執行完成", "已自動登入並完成打卡！"))
                self.root.after(0, lambda: self.status_label.config(text="打卡完成", fg="red"))
            # ===========================================

        except Exception as e:
            err_msg = str(e)
            self.root.after(0, lambda: messagebox.showerror("執行失敗", f"發生錯誤: {err_msg}"))
        finally:
            self.is_running = False
            self.root.after(0, self.reset_buttons)

if __name__ == "__main__":
    root = tk.Tk()
    app = NueipChromeClickerV4(root)
    root.mainloop()
