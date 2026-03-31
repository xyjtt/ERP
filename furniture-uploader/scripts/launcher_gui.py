from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class LauncherApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.project_root = Path(__file__).resolve().parents[1]
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.run_process: Optional[subprocess.Popen[str]] = None
        self.run_thread: Optional[threading.Thread] = None

        self.status_var = tk.StringVar(value="就绪")
        self.template_var = tk.StringVar(value=str(self._resolve_default_template()))
        self.system_var = tk.StringVar(value="1688_direct")
        self.platform_var = tk.StringVar(value="1688")
        self.input_mode_value_by_label = {
            "变体": "variant",
            "商品": "product",
            "自动": "auto",
        }
        self.input_mode_label_by_value = {
            value: label for label, value in self.input_mode_value_by_label.items()
        }
        self.input_mode_display_var = tk.StringVar(
            value=self.input_mode_label_by_value["variant"]
        )
        self.limit_var = tk.StringVar(value="0")
        self.skip_login_var = tk.BooleanVar(value=True)
        self.no_notify_var = tk.BooleanVar(value=True)

        self.root.title("1688 上架工具（单机双击版）")
        self.root.geometry("980x720")
        self.root.minsize(860, 620)

        self._build_ui()
        self._append_log(f"[INFO] 项目目录: {self.project_root}")
        self._append_log(f"[INFO] Python 解释器: {sys.executable}")
        self._append_log("[INFO] 请先点击“启动 Edge 调试窗口”并在浏览器完成登录。")
        self.root.after(120, self._poll_log_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        form = ttk.LabelFrame(container, text="任务参数", padding=10)
        form.pack(fill=tk.X)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="模板文件").grid(row=0, column=0, sticky=tk.W, pady=4)
        template_entry = ttk.Entry(form, textvariable=self.template_var)
        template_entry.grid(row=0, column=1, sticky=tk.EW, padx=8, pady=4)
        ttk.Button(form, text="选择文件", command=self._choose_template).grid(row=0, column=2, pady=4)

        ttk.Label(form, text="执行系统").grid(row=1, column=0, sticky=tk.W, pady=4)
        system_cb = ttk.Combobox(
            form,
            textvariable=self.system_var,
            values=["1688_direct", "jushuitan"],
            state="readonly",
            width=20,
        )
        system_cb.grid(row=1, column=1, sticky=tk.W, padx=8, pady=4)

        ttk.Label(form, text="目标平台").grid(row=2, column=0, sticky=tk.W, pady=4)
        platform_cb = ttk.Combobox(
            form,
            textvariable=self.platform_var,
            values=["1688", "alibaba", "alibaba1688", "taobao", "jd", "pdd"],
            state="readonly",
            width=20,
        )
        platform_cb.grid(row=2, column=1, sticky=tk.W, padx=8, pady=4)

        ttk.Label(form, text="输入模式").grid(row=3, column=0, sticky=tk.W, pady=4)
        input_mode_cb = ttk.Combobox(
            form,
            textvariable=self.input_mode_display_var,
            values=list(self.input_mode_value_by_label.keys()),
            state="readonly",
            width=20,
        )
        input_mode_cb.grid(row=3, column=1, sticky=tk.W, padx=8, pady=4)

        ttk.Label(form, text="处理条数").grid(row=4, column=0, sticky=tk.W, pady=4)
        ttk.Entry(form, textvariable=self.limit_var, width=22).grid(row=4, column=1, sticky=tk.W, padx=8, pady=4)
        ttk.Label(form, text="0 表示不限数量").grid(row=4, column=2, sticky=tk.W, pady=4)

        options = ttk.Frame(form)
        options.grid(row=5, column=1, columnspan=2, sticky=tk.W, padx=8, pady=8)
        ttk.Checkbutton(options, text="跳过登录（复用已登录浏览器）", variable=self.skip_login_var).pack(
            anchor=tk.W
        )
        ttk.Checkbutton(options, text="禁用钉钉通知（推荐本地调试时勾选）", variable=self.no_notify_var).pack(
            anchor=tk.W
        )

        button_bar = ttk.LabelFrame(container, text="操作", padding=10)
        button_bar.pack(fill=tk.X, pady=(10, 0))

        self.edge_btn = ttk.Button(button_bar, text="启动Edge调试窗口", command=self._launch_edge_debug)
        self.edge_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.start_btn = ttk.Button(button_bar, text="开始上架任务", command=self._start_task)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = ttk.Button(button_bar, text="停止任务", command=self._stop_task, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.report_btn = ttk.Button(button_bar, text="打开运行报告目录", command=self._open_run_report_dir)
        self.report_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.logs_btn = ttk.Button(button_bar, text="打开日志目录", command=self._open_logs_dir)
        self.logs_btn.pack(side=tk.LEFT, padx=(0, 8))

        status_bar = ttk.Frame(container)
        status_bar.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(status_bar, text="状态:").pack(side=tk.LEFT)
        ttk.Label(status_bar, textvariable=self.status_var).pack(side=tk.LEFT, padx=(6, 0))

        log_frame = ttk.LabelFrame(container, text="运行日志", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, wrap=tk.WORD, font=("Consolas", 10))
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky=tk.NS)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.configure(state=tk.DISABLED)

    def _resolve_default_template(self) -> Path:
        candidates = [
            self.project_root / "templates" / "furniture_template.csv",
            self.project_root / "templates" / "1688_corner_table_smoke.csv",
            self.project_root / "templates" / "1688_variant_sample.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return self.project_root / "templates"

    def _append_log(self, message: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _poll_log_queue(self) -> None:
        while True:
            try:
                item = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(item)
        self.root.after(120, self._poll_log_queue)

    def _set_task_running(self, running: bool) -> None:
        if running:
            self.start_btn.configure(state=tk.DISABLED)
            self.stop_btn.configure(state=tk.NORMAL)
            self.status_var.set("任务运行中")
        else:
            self.start_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.DISABLED)
            self.status_var.set("就绪")

    def _choose_template(self) -> None:
        selected = filedialog.askopenfilename(
            title="选择上架模板文件",
            initialdir=str(self.project_root / "templates"),
            filetypes=[
                ("模板文件", "*.csv *.xlsx *.json"),
                ("CSV 文件", "*.csv"),
                ("Excel 文件", "*.xlsx"),
                ("JSON 文件", "*.json"),
                ("所有文件", "*.*"),
            ],
        )
        if not selected:
            return
        self.template_var.set(selected)
        suffix = Path(selected).suffix.lower()
        if suffix == ".json":
            self.input_mode_display_var.set(self.input_mode_label_by_value["variant"])
        elif suffix in {".csv", ".xlsx"}:
            self.input_mode_display_var.set(self.input_mode_label_by_value["product"])

    def _open_folder(self, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        os.startfile(str(target))

    def _resolve_run_report_dir(self) -> Path:
        run_report_dir = "logs/run_reports"
        config_dir = self.project_root / "config"
        for file_name in ("operator_config.json", "operator_config.local.json"):
            path = config_dir / file_name
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            runtime = payload.get("runtime", {})
            if isinstance(runtime, dict):
                value = runtime.get("run_report_dir", "")
                if isinstance(value, str) and value.strip():
                    run_report_dir = value.strip()
        return (self.project_root / run_report_dir).resolve()

    def _open_run_report_dir(self) -> None:
        self._open_folder(self._resolve_run_report_dir())

    def _open_logs_dir(self) -> None:
        self._open_folder((self.project_root / "logs").resolve())

    def _launch_edge_debug(self) -> None:
        threading.Thread(target=self._launch_edge_debug_worker, daemon=True).start()

    def _launch_edge_debug_worker(self) -> None:
        script_path = self.project_root / "scripts" / "start_debug_edge.ps1"
        if not script_path.exists():
            self.log_queue.put(f"[ERROR] 脚本不存在: {script_path}")
            return
        command = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ]
        self.log_queue.put(f"[CMD] {subprocess.list2cmdline(command)}")
        completed = subprocess.run(
            command,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.stdout:
            for line in completed.stdout.splitlines():
                self.log_queue.put(line)
        if completed.stderr:
            for line in completed.stderr.splitlines():
                self.log_queue.put(f"[STDERR] {line}")
        if completed.returncode == 0:
            self.log_queue.put("[INFO] Edge 调试窗口已启动，请在新窗口完成 1688 登录。")
        else:
            self.log_queue.put(f"[ERROR] 启动 Edge 调试窗口失败，退出码: {completed.returncode}")

    def _build_main_command(self) -> list[str]:
        template_path = Path(self.template_var.get().strip('"').strip())
        if not template_path.is_absolute():
            template_path = (self.project_root / template_path).resolve()
        if not template_path.exists():
            raise ValueError(f"模板文件不存在: {template_path}")
        input_mode_display = self.input_mode_display_var.get().strip()
        input_mode = self.input_mode_value_by_label.get(input_mode_display, "")
        if not input_mode:
            raise ValueError("输入模式无效，请选择“变体 / 商品 / 自动”。")
        self._validate_template_mode(template_path, input_mode)

        raw_limit = self.limit_var.get().strip()
        if not raw_limit:
            parsed_limit = 0
        else:
            try:
                parsed_limit = int(raw_limit)
            except ValueError as exc:
                raise ValueError("处理条数必须是整数。") from exc
            if parsed_limit < 0:
                raise ValueError("处理条数不能小于 0。")

        command = [
            sys.executable,
            "-X",
            "utf8",
            "rpa/main.py",
            "--system",
            self.system_var.get().strip(),
            "--platform",
            self.platform_var.get().strip(),
            "--file",
            str(template_path),
            "--input-mode",
            input_mode,
        ]
        if parsed_limit > 0:
            command.extend(["--limit", str(parsed_limit)])
        if self.skip_login_var.get():
            command.append("--skip-login")
        if self.no_notify_var.get():
            command.append("--no-notify")
        return command

    def _validate_template_mode(self, template_path: Path, input_mode: str) -> None:
        suffix = template_path.suffix.lower()
        product_suffixes = {".csv", ".xlsx", ".xls"}
        variant_suffixes = {".json", ".csv", ".xlsx", ".xls"}

        if input_mode == "product" and suffix not in product_suffixes:
            raise ValueError(
                f"商品模式不支持 {suffix} 文件。请选择 CSV/XLSX，或把输入模式切换到“变体”。"
            )
        if input_mode == "variant" and suffix not in variant_suffixes:
            raise ValueError(
                f"变体模式不支持 {suffix} 文件。请选择 JSON/CSV/XLSX。"
            )

    def _start_task(self) -> None:
        if self.run_process and self.run_process.poll() is None:
            messagebox.showwarning("任务运行中", "当前已有任务在运行。")
            return

        try:
            command = self._build_main_command()
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc))
            return

        self._set_task_running(True)
        self.log_queue.put("")
        self.log_queue.put("[INFO] 开始执行上架任务...")
        self.log_queue.put(f"[CMD] {subprocess.list2cmdline(command)}")
        self.run_thread = threading.Thread(target=self._run_task_worker, args=(command,), daemon=True)
        self.run_thread.start()

    def _run_task_worker(self, command: list[str]) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            process = subprocess.Popen(
                command,
                cwd=self.project_root,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            self.log_queue.put(f"[ERROR] 启动任务失败: {exc}")
            self.root.after(0, lambda: self._set_task_running(False))
            return

        self.run_process = process
        assert process.stdout is not None
        for line in process.stdout:
            self.log_queue.put(line.rstrip("\r\n"))
        process.wait()

        exit_code = process.returncode
        self.run_process = None
        self.root.after(0, lambda: self._set_task_running(False))
        if exit_code == 0:
            self.log_queue.put("[DONE] 任务已完成。")
        else:
            self.log_queue.put(f"[ERROR] 任务执行失败，退出码: {exit_code}")

    def _stop_task(self) -> None:
        process = self.run_process
        if not process or process.poll() is not None:
            self._set_task_running(False)
            return
        self.log_queue.put("[INFO] 正在停止任务...")
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        self.log_queue.put("[INFO] 任务已停止。")
        self._set_task_running(False)

    def _on_close(self) -> None:
        process = self.run_process
        if process and process.poll() is None:
            should_quit = messagebox.askyesno("退出确认", "任务仍在运行，是否停止任务并退出？")
            if not should_quit:
                return
            self._stop_task()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    style = ttk.Style()
    if "vista" in style.theme_names():
        style.theme_use("vista")
    app = LauncherApp(root)
    _ = app
    root.mainloop()


if __name__ == "__main__":
    main()
