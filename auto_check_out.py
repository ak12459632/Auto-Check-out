"""
社畜神器 v1.2
修正項目：
  - 上班/下班按鈕 XPath 改用直接子節點標題定位，避免比對整棵子樹
    造成誤中公告區或其他含「上班」文字的元素
  - 新增多組候選 XPath fallback，依序嘗試，提高跨頁面版本相容性
"""

import tkinter as tk
from tkinter import messagebox, scrolledtext
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
import json
import logging

# ── 常數 ──────────────────────────────────────────────────────────────
CONFIG_FILE = "user_config.json"
LOG_FILE    = "punch_log.txt"

# 多組候選 XPath：依序嘗試，第一個成功的即採用
# 順序根據實測結果調整：class/id 含 punch 的容器已確認可命中，優先放最前
CHECKIN_XPATHS = [
    # 策略1【已確認可用】：class/id 含 punch 的容器內找「上班」文字
    "//*[contains(@class,'punch') or contains(@id,'punch')]//*[normalize-space(text())='上班']",
    # 策略2：直接找 button/a 精確等於「上班」（語意化按鈕版本）
    "//*[self::button or self::a][normalize-space()='上班']",
    # 策略3：找直接子節點為「打卡鐘」標題的 div（heading 版本）
    "//div[h3[normalize-space()='打卡鐘'] or h4[normalize-space()='打卡鐘'] or h2[normalize-space()='打卡鐘']][1]//*[normalize-space(text())='上班']",
    # 策略4：section 標題後的 following:: 軸（heading 版本備援）
    "//*[self::h2 or self::h3 or self::h4][normalize-space()='打卡鐘']/following::*[normalize-space(text())='上班'][1]",
]

CHECKOUT_XPATHS = [
    # 策略1【已確認可用】
    "//*[contains(@class,'punch') or contains(@id,'punch')]//*[normalize-space(text())='下班']",
    "//*[self::button or self::a][normalize-space()='下班']",
    "//div[h3[normalize-space()='打卡鐘'] or h4[normalize-space()='打卡鐘'] or h2[normalize-space()='打卡鐘']][1]//*[normalize-space(text())='下班']",
    "//*[self::h2 or self::h3 or self::h4][normalize-space()='打卡鐘']/following::*[normalize-space(text())='下班'][1]",
]

# ── 日誌設定（寫入檔案） ───────────────────────────────────────────────
logger = logging.getLogger("NueipV5p1")
logger.setLevel(logging.INFO)
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)


# ══════════════════════════════════════════════════════════════════════
class NueipChromeClickerV5p1:
    def __init__(self, root):
        self.root = root
        self.root.title("社畜神器 v1.2 (上下班雙排程)")
        self.root.geometry("680x960")

        self.is_running   = False
        self._active_tasks = 0
        self._tasks_lock   = threading.Lock()

        self._build_ui()
        self.load_config()
        self.log("程式啟動完成")

    # ── UI 建構 ────────────────────────────────────────────────────────
    def _build_ui(self):
        root = self.root

        tk.Label(root, text="社畜 v1.2",
                 font=("Arial", 16, "bold"), fg="#1a73e8").pack(pady=10)

        # ── 第一步：登入資訊 ──────────────────────────────────────────
        frame_auth = tk.LabelFrame(root, text="第一步：登入資訊（自動記憶）",
                                   padx=10, pady=8, fg="blue")
        frame_auth.pack(padx=10, fill="x")

        tk.Label(frame_auth, text="公司代碼:").grid(row=0, column=0, sticky="w")
        self.entry_company = tk.Entry(frame_auth, width=32)
        self.entry_company.grid(row=0, column=1, pady=2, sticky="w")

        tk.Label(frame_auth, text="員工編號:").grid(row=1, column=0, sticky="w")
        self.entry_user = tk.Entry(frame_auth, width=32)
        self.entry_user.grid(row=1, column=1, pady=2, sticky="w")

        tk.Label(frame_auth, text="登入密碼:").grid(row=2, column=0, sticky="w")
        self.entry_pass = tk.Entry(frame_auth, width=32, show="*")
        self.entry_pass.grid(row=2, column=1, pady=2, sticky="w")

        tk.Label(frame_auth, text="帳號密碼將自動存入 user_config.json",
                 fg="gray", font=("Arial", 8)).grid(row=3, column=1, sticky="w", pady=2)

        # ── 第二步：排程設定 ──────────────────────────────────────────
        frame_input = tk.LabelFrame(root, text="第二步：排程設定", padx=10, pady=8)
        frame_input.pack(padx=10, pady=6, fill="x")

        tk.Label(frame_input, text="登入網址:").grid(row=0, column=0, sticky="w")
        self.entry_url = tk.Entry(frame_input, width=44)
        self.entry_url.insert(0, "https://cloud.nueip.com/login")
        self.entry_url.grid(row=0, column=1, columnspan=2, pady=4, sticky="w")

        # 上班打卡
        self.checkin_enabled = tk.BooleanVar(value=False)
        tk.Checkbutton(frame_input, text="上班打卡",
                       variable=self.checkin_enabled,
                       fg="#1565c0", font=("Arial", 10, "bold")
                       ).grid(row=1, column=0, sticky="w")
        self.entry_checkin_time = tk.Entry(frame_input, width=12)
        self.entry_checkin_time.insert(0, "09:00")
        self.entry_checkin_time.grid(row=1, column=1, pady=3, sticky="w")
        tk.Label(frame_input, text="HH:MM", fg="gray"
                 ).grid(row=1, column=2, sticky="w", padx=4)

        # 下班打卡
        self.checkout_enabled = tk.BooleanVar(value=True)
        tk.Checkbutton(frame_input, text="下班打卡",
                       variable=self.checkout_enabled,
                       fg="#b71c1c", font=("Arial", 10, "bold")
                       ).grid(row=2, column=0, sticky="w")
        self.entry_checkout_time = tk.Entry(frame_input, width=12)
        self.entry_checkout_time.insert(0, "18:00")
        self.entry_checkout_time.grid(row=2, column=1, pady=3, sticky="w")
        tk.Label(frame_input, text="HH:MM", fg="gray"
                 ).grid(row=2, column=2, sticky="w", padx=4)

        # 保持測試模式
        self.test_mode_var = tk.BooleanVar(value=True)
        tk.Checkbutton(frame_input,
                       text="保持測試模式（只標記按鈕，不實際點擊）",
                       variable=self.test_mode_var,
                       fg="red", font=("Arial", 10, "bold")
                       ).grid(row=3, column=1, columnspan=2, sticky="w", pady=5)

        # ── 進階 XPath ────────────────────────────────────────────────
        frame_xpath = tk.LabelFrame(root, text="進階 XPath 定位（預設已優化）",
                                    padx=10, pady=8)
        frame_xpath.pack(padx=10, fill="x")

        xp_defs = [
            ("代碼輸入框:", "xp_company",
             "//input[@placeholder='公司代碼']"),
            ("帳號輸入框:", "xp_user",
             "//input[@placeholder='員工編號' or @name='dept_input']"),
            ("密碼輸入框:", "xp_pass",
             "//input[@type='password']"),
            ("登入按鈕:",   "xp_login_btn",
             "//button[contains(., '登入') or @type='submit']"),
            ("上班按鈕:",   "xp_checkin",  CHECKIN_XPATHS[0]),
            ("下班按鈕:",   "xp_clockout", CHECKOUT_XPATHS[0]),
        ]
        for i, (label, attr, default) in enumerate(xp_defs):
            tk.Label(frame_xpath, text=label).grid(row=i, column=0, sticky="w")
            entry = tk.Entry(frame_xpath, width=48)
            entry.insert(0, default)
            entry.grid(row=i, column=1, pady=1)
            setattr(self, attr, entry)

        # ── 控制按鈕 ──────────────────────────────────────────────────
        frame_ctrl = tk.Frame(root, pady=8)
        frame_ctrl.pack(fill="x", padx=20)

        self.status_label = tk.Label(root, text="狀態：準備就緒",
                                     fg="gray", font=("Arial", 11))
        self.status_label.pack(pady=3)

        self.btn_start = tk.Button(frame_ctrl, text="儲存並啟動排程",
                                   command=self.start_schedule,
                                   bg="#e8f0fe", font=("Arial", 12), height=2)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=5)

        self.btn_cancel = tk.Button(frame_ctrl, text="取消 / 停止",
                                    command=self.cancel_schedule,
                                    bg="#ffcdd2", fg="red",
                                    font=("Arial", 12), state="disabled", height=2)
        self.btn_cancel.pack(side="right", fill="x", expand=True, padx=5)

        # ── 日誌區 ────────────────────────────────────────────────────
        log_frame = tk.LabelFrame(root, text=f"執行日誌（同步寫入 {LOG_FILE}）",
                                  padx=5, pady=5)
        log_frame.pack(padx=10, pady=5, fill="both", expand=True)

        self.log_text = scrolledtext.ScrolledText(
            log_frame, height=9, state="disabled",
            font=("Courier", 9), bg="#f5f5f5")
        self.log_text.pack(fill="both", expand=True)

    # ── 日誌工具 ───────────────────────────────────────────────────────
    def log(self, msg: str, level: str = "INFO"):
        """同時寫入 punch_log.txt 與 UI 文字框。"""
        getattr(logger, level.lower(), logger.info)(msg)
        icons  = {"INFO": "i", "ERROR": "X", "WARNING": "!"}
        ts     = datetime.now().strftime("%H:%M:%S")
        entry  = f"[{ts}] {icons.get(level, '')} {msg}\n"
        self.root.after(0, self._append_log, entry)

    def _append_log(self, entry: str):
        self.log_text.config(state="normal")
        self.log_text.insert(tk.END, entry)
        self.log_text.see(tk.END)
        self.log_text.config(state="disabled")

    # ── 設定檔 ────────────────────────────────────────────────────────
    def load_config(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            for key, entry in [
                ("company",       self.entry_company),
                ("url",           self.entry_url),
                ("checkin_time",  self.entry_checkin_time),
                ("checkout_time", self.entry_checkout_time),
            ]:
                if key in data:
                    entry.delete(0, tk.END)
                    entry.insert(0, data[key])

            if "user" in data:
                self.entry_user.delete(0, tk.END)
                self.entry_user.insert(0, data["user"])
            if "password" in data:
                self.entry_pass.delete(0, tk.END)
                self.entry_pass.insert(0, data["password"])

            if "checkin_enabled" in data:
                self.checkin_enabled.set(data["checkin_enabled"])
            if "checkout_enabled" in data:
                self.checkout_enabled.set(data["checkout_enabled"])

            self.log("設定檔讀取完成")
        except Exception as e:
            self.log(f"讀取設定失敗: {e}", "ERROR")

    def save_config(self):
        data = {
            "company":          self.entry_company.get(),
            "user":             self.entry_user.get(),
            "password":         self.entry_pass.get(),
            "url":              self.entry_url.get(),
            "checkin_time":     self.entry_checkin_time.get(),
            "checkout_time":    self.entry_checkout_time.get(),
            "checkin_enabled":  self.checkin_enabled.get(),
            "checkout_enabled": self.checkout_enabled.get(),
        }
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            self.log("設定檔已儲存")
        except Exception as e:
            self.log(f"儲存設定失敗: {e}", "ERROR")

    # ── 時間解析（已過期自動改隔天）───────────────────────────────────
    def _parse_trigger_time(self, time_str: str, label: str) -> datetime:
        target = datetime.strptime(time_str, "%H:%M")
        now    = datetime.now()
        target = target.replace(year=now.year, month=now.month, day=now.day)

        if target <= now:
            target += timedelta(days=1)
            self.log(
                f"[{label}] {time_str} 今日已過，自動改為明天 "
                f"{target.strftime('%m/%d %H:%M')} 執行",
                "WARNING"
            )
        else:
            self.log(f"[{label}] 排程時間：{target.strftime('%m/%d %H:%M:%S')}")

        return target

    # ── 核心：多組 XPath fallback 定位按鈕 ───────────────────────────
    def _find_punch_button(self, driver, action: str):
        """
        依序嘗試 CHECKIN_XPATHS / CHECKOUT_XPATHS 清單。
        UI 欄位的自訂 XPath 排在最前面，其餘候選接在後面。
        回傳第一個成功找到的 WebElement；全部失敗則 raise Exception。
        """
        label = "上班" if action == "checkin" else "下班"

        # UI 自訂值優先，後面接預設候選清單（去重）
        ui_xpath   = (self.xp_checkin.get() if action == "checkin"
                      else self.xp_clockout.get())
        candidates = (CHECKIN_XPATHS if action == "checkin" else CHECKOUT_XPATHS)
        xpaths     = [ui_xpath] + [x for x in candidates if x != ui_xpath]

        wait_short = WebDriverWait(driver, 5)

        for i, xp in enumerate(xpaths, 1):
            try:
                btn = wait_short.until(
                    EC.element_to_be_clickable((By.XPATH, xp))
                )
                self.log(f"[{label}] XPath 候選 #{i} 定位成功")
                return btn
            except Exception:
                self.log(f"[{label}] XPath 候選 #{i} 未命中，嘗試下一組…", "WARNING")

        raise Exception(
            f"所有 {len(xpaths)} 組 XPath 均無法定位 [{label}] 按鈕，"
            "請用 F12 確認實際 HTML 並更新 XPath 欄位"
        )

    # ── 啟動流程 ───────────────────────────────────────────────────────
    def start_schedule(self):
        self.save_config()

        if not all([self.entry_company.get(),
                    self.entry_user.get(),
                    self.entry_pass.get()]):
            messagebox.showwarning("提醒", "請填入公司代碼、員工編號與密碼！")
            return
        if not self.checkin_enabled.get() and not self.checkout_enabled.get():
            messagebox.showwarning("提醒", "請至少勾選一個打卡項目（上班或下班）！")
            return

        try:
            self.schedule_tasks: dict[str, datetime] = {}
            if self.checkin_enabled.get():
                self.schedule_tasks["checkin"] = self._parse_trigger_time(
                    self.entry_checkin_time.get(), "上班")
            if self.checkout_enabled.get():
                self.schedule_tasks["checkout"] = self._parse_trigger_time(
                    self.entry_checkout_time.get(), "下班")
        except ValueError:
            messagebox.showerror("錯誤", "時間格式錯誤，請使用 HH:MM 格式")
            return

        self.is_running = True
        self.btn_start.config(state="disabled")
        self.btn_cancel.config(state="normal")

        self.log("=" * 50)
        self.log("啟動前自動執行測試模式，確認按鈕定位…")
        self.root.after(0, lambda: self.status_label.config(
            text="測試中，請稍候…", fg="orange"))

        threading.Thread(target=self._test_then_schedule, daemon=True).start()

    def _test_then_schedule(self):
        test_action = "checkin" if self.checkin_enabled.get() else "checkout"
        success = self._run_browser(action=test_action, is_test=True)

        if not self.is_running:
            return

        if success:
            self.log("測試通過！正式排程已啟動")
            self.root.after(0, lambda: self.status_label.config(
                text="測試通過，等待排程時間…", fg="green"))

            with self._tasks_lock:
                self._active_tasks = len(self.schedule_tasks)

            for action, trigger_dt in self.schedule_tasks.items():
                threading.Thread(
                    target=self._wait_loop,
                    args=(trigger_dt, action),
                    daemon=True
                ).start()
        else:
            self.log("測試失敗，排程未啟動。請確認 XPath 與登入資訊", "ERROR")
            self.root.after(0, lambda: self.status_label.config(
                text="測試失敗，請確認設定後重試", fg="red"))
            self.is_running = False
            self.root.after(0, self.reset_buttons)

    # ── 倒數等待迴圈 ───────────────────────────────────────────────────
    def _wait_loop(self, trigger_dt: datetime, action: str):
        label = "上班" if action == "checkin" else "下班"
        self.log(f"[{label}] 倒數開始，目標：{trigger_dt.strftime('%H:%M:%S')}")

        while self.is_running:
            remaining = (trigger_dt - datetime.now()).total_seconds()

            if remaining <= 0:
                if self.is_running:
                    self.log(f"[{label}] 時間到，開始執行打卡")
                    is_test = self.test_mode_var.get()
                    self._run_browser(action=action, is_test=is_test)
                break

            sleep_t = 0.5 if remaining <= 10 else min(remaining - 5, 5)
            self.root.after(0, lambda r=remaining, l=label:
                            self.status_label.config(text=f"[{l}] 倒數：{int(r)} 秒"))
            time.sleep(sleep_t)

        with self._tasks_lock:
            self._active_tasks = max(self._active_tasks - 1, 0)
            all_done = (self._active_tasks == 0)

        if all_done and self.is_running:
            self.is_running = False
            self.log("所有排程任務已完成")
            self.root.after(0, self.reset_buttons)

    # ── 核心：開啟瀏覽器執行打卡 ──────────────────────────────────────
    def _run_browser(self, action: str, is_test: bool) -> bool:
        label = "上班" if action == "checkin" else "下班"
        mode  = "【測試模式】" if is_test else "【正式模式】"
        self.log(f"{mode} 開始執行 [{label}] 打卡流程")
        self.root.after(0, lambda: self.status_label.config(
            text=f"{mode} 啟動 Chrome…", fg="orange"))

        try:
            options = Options()
            options.add_experimental_option("detach", True)
            options.add_argument("--start-maximized")

            service = Service(ChromeDriverManager().install())
            driver  = webdriver.Chrome(service=service, options=options)
            wait    = WebDriverWait(driver, 20)

            # 1. 開啟登入頁
            driver.get(self.entry_url.get())
            self.log(f"已開啟頁面：{self.entry_url.get()}")
            self.root.after(0, lambda: self.status_label.config(
                text="輸入帳號密碼中…"))

            # 2. 填入帳密
            elem = wait.until(EC.visibility_of_element_located(
                (By.XPATH, self.xp_company.get())))
            elem.clear()
            elem.send_keys(self.entry_company.get())

            elem = driver.find_element(By.XPATH, self.xp_user.get())
            elem.clear()
            elem.send_keys(self.entry_user.get())

            elem = driver.find_element(By.XPATH, self.xp_pass.get())
            elem.clear()
            elem.send_keys(self.entry_pass.get())
            time.sleep(0.5)

            # 3. 點擊登入
            driver.find_element(By.XPATH, self.xp_login_btn.get()).click()
            self.log("已點擊登入按鈕，等待頁面跳轉…")
            self.root.after(0, lambda: self.status_label.config(
                text="登入中，等待頁面跳轉…"))

            # 4. 等待頁面穩定後，用 fallback 機制定位打卡按鈕
            time.sleep(2)
            btn = self._find_punch_button(driver, action)
            driver.execute_script("arguments[0].scrollIntoView();", btn)
            time.sleep(0.5)
            self.log(f"已找到 [{label}] 按鈕")

            # 5a. 測試模式：標記不點擊
            if is_test:
                driver.execute_script(
                    "arguments[0].style.border='5px solid red';", btn)
                driver.execute_script(
                    "arguments[0].style.backgroundColor='yellow';", btn)
                self.log(f"【測試】已用紅框黃底標記 [{label}] 按鈕（未點擊）")
                self.root.after(0, lambda: messagebox.showinfo(
                    "測試成功",
                    f"自動登入成功！\n已找到【{label}】打卡按鈕。\n\n"
                    f"請確認網頁上的紅框標記位置是否正確。\n\n"
                    f"正式模式將自動點擊該按鈕完成打卡。"
                ))
                self.root.after(0, lambda: self.status_label.config(
                    text=f"測試完成－[{label}] 按鈕定位成功", fg="green"))
                return True

            # 5b. 正式模式：實際點擊
            else:
                btn.click()
                self.log(f"【正式】已點擊 [{label}] 打卡按鈕，打卡完成！")
                self.root.after(0, lambda: messagebox.showinfo(
                    "打卡完成", f"【{label}】打卡成功！"))
                self.root.after(0, lambda: self.status_label.config(
                    text=f"[{label}] 打卡完成", fg="green"))
                return True

        except Exception as e:
            err = str(e)
            self.log(f"[{label}] 執行失敗：{err}", "ERROR")
            self.root.after(0, lambda msg=err: messagebox.showerror(
                "執行失敗", f"[{label}] 發生錯誤：\n{msg}"))
            return False

    # ── 取消 / 重設 ────────────────────────────────────────────────────
    def cancel_schedule(self):
        if self.is_running:
            self.is_running = False
            self.log("使用者手動取消排程", "WARNING")
            self.root.after(0, lambda: self.status_label.config(
                text="已取消排程", fg="red"))
            messagebox.showinfo("取消", "已停止所有排程任務。")
            self.reset_buttons()

    def reset_buttons(self):
        self.btn_start.config(state="normal", text="儲存並啟動排程")
        self.btn_cancel.config(state="disabled")


# ══════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    root = tk.Tk()
    app  = NueipChromeClickerV5p1(root)
    root.mainloop()
