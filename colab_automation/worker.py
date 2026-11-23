"""Colab job worker: runs jobs dropped into a Google Drive folder and writes results back.

Drive layout (under MyDrive/Colab_Automation):
    inbox/    job_<id>.txt       jobs waiting to run (cancel_<id>.txt stops a running job)
    outbox/   result_<id>.txt    final result;  progress_<id>.txt while a job is running
    done/     processed job files
    outputs/  anything a job wants to keep
    status.txt                   heartbeat, rewritten about once a minute

Job file: first line is `#!job {"id": "...", "type": "python|shell|background", "timeout": 3600}`,
everything after it is the code.  `background` starts the code as a shell script and returns
at once (for servers such as ComfyUI); its log is /content/jobs/<id>/output.log.
"""
import json
import os
import signal
import subprocess
import sys
import time

FOLDER = "Colab_Automation"
BASE = os.environ.get("COLAB_AUTO_DIR", "/content/drive/MyDrive/" + FOLDER)
WORK = os.environ.get("COLAB_AUTO_JOBS", "/content/jobs")
MODE = os.environ.get("COLAB_AUTO_MODE", "auto")  # auto | api | fuse
POLL_SECONDS = float(os.environ.get("COLAB_AUTO_POLL", "5"))
PROGRESS_SECONDS = float(os.environ.get("COLAB_AUTO_PROGRESS", "45"))
HEARTBEAT_SECONDS = 60
TAIL_CHARS = 20000
SUBFOLDERS = ("inbox", "outbox", "done", "outputs")


class FuseStore:
    """Queue on the mounted Drive folder. Simple, but Drive sync can lag by a minute or more."""

    name = "fuse"

    def __init__(self):
        for sub in SUBFOLDERS:
            os.makedirs(os.path.join(BASE, sub), exist_ok=True)

    def list_inbox(self):
        inbox = os.path.join(BASE, "inbox")
        return [(os.path.join(inbox, n), n) for n in sorted(os.listdir(inbox))]

    def read(self, handle):
        with open(handle, encoding="utf-8") as f:
            return f.read()

    def finish(self, handle):
        os.replace(handle, os.path.join(BASE, "done", os.path.basename(handle)))

    def write(self, folder, name, text):
        path = os.path.join(BASE, folder, name)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(path + ".tmp", path)

    def remove(self, folder, name):
        try:
            os.remove(os.path.join(BASE, folder, name))
        except FileNotFoundError:
            pass


class ApiStore:
    """Queue through the Drive API with the signed-in Colab user. No sync lag."""

    name = "api"

    def __init__(self):
        import google.auth
        from googleapiclient.discovery import build

        creds, _ = google.auth.default()
        self.files = build("drive", "v3", credentials=creds, cache_discovery=False).files()
        self.ids = {"": self._folder(FOLDER, "root")}
        for sub in SUBFOLDERS:
            self.ids[sub] = self._folder(sub, self.ids[""])
        self.known = {}  # (folder, name) -> file id

    def _find(self, name, parent):
        query = "name = '%s' and '%s' in parents and trashed = false" % (name, parent)
        found = self.files.list(q=query, fields="files(id)").execute().get("files", [])
        return found[0]["id"] if found else None

    def _folder(self, name, parent):
        found = self._find(name, parent)
        if found:
            return found
        meta = {"name": name, "parents": [parent], "mimeType": "application/vnd.google-apps.folder"}
        return self.files.create(body=meta, fields="id").execute()["id"]

    def list_inbox(self):
        query = "'%s' in parents and trashed = false" % self.ids["inbox"]
        found = self.files.list(q=query, fields="files(id,name)", pageSize=200).execute()
        return sorted(((f["id"], f["name"]) for f in found.get("files", [])), key=lambda f: f[1])

    def read(self, handle):
        data = self.files.get_media(fileId=handle).execute()
        return data.decode("utf-8") if isinstance(data, bytes) else data

    def finish(self, handle):
        self.files.update(
            fileId=handle, addParents=self.ids["done"], removeParents=self.ids["inbox"]
        ).execute()

    def write(self, folder, name, text):
        from googleapiclient.http import MediaInMemoryUpload

        media = MediaInMemoryUpload(text.encode("utf-8"), mimetype="text/plain")
        key = (folder, name)
        if key not in self.known:
            self.known[key] = self._find(name, self.ids[folder])
        if self.known[key]:
            self.files.update(fileId=self.known[key], media_body=media).execute()
        else:
            meta = {"name": name, "parents": [self.ids[folder]]}
            created = self.files.create(body=meta, media_body=media, fields="id").execute()
            self.known[key] = created["id"]

    def remove(self, folder, name):
        key = (folder, name)
        file_id = self.known.pop(key, None) or self._find(name, self.ids[folder])
        if file_id:
            self.files.delete(fileId=file_id).execute()


def make_store():
    if MODE in ("auto", "api"):
        try:
            return ApiStore()
        except Exception as error:
            if MODE == "api":
                raise
            print("[worker] Drive API unavailable (%s); using the mounted folder instead" % error)
    return FuseStore()


def parse_job(name, text):
    header, _, code = text.partition("\n")
    if not header.startswith("#!job"):
        raise ValueError("first line must start with '#!job {json}'")
    meta = json.loads(header[len("#!job"):].strip() or "{}")
    meta.setdefault("id", os.path.splitext(name)[0][len("job_"):])
    meta.setdefault("type", "python")
    meta.setdefault("timeout", 3600)
    if meta["type"] not in ("python", "shell", "background"):
        raise ValueError("unknown job type %r" % meta["type"])
    if not all(c.isalnum() or c in "-_" for c in str(meta["id"])):
        raise ValueError("job id may only contain letters, digits, - and _")
    return meta, code


def tail(path, limit=TAIL_CHARS):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit * 4))
            text = f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return ""
    if len(text) > limit:
        text = "...[earlier output cut; full log: %s]...\n" % path + text[-limit:]
    return text


def report(job_id, status, seconds, log_path, extra=""):
    head = "JOB %s | status: %s | seconds: %.1f%s" % (job_id, status, seconds, extra)
    return head + "\n--- OUTPUT ---\n" + tail(log_path)


def stop(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_job(store, meta, code, background):
    job_id = str(meta["id"])
    workdir = os.path.join(WORK, job_id)
    os.makedirs(workdir, exist_ok=True)
    log_path = os.path.join(workdir, "output.log")
    if meta["type"] == "python":
        script, command = "job.py", [sys.executable, "-u", "job.py"]
    else:
        script, command = "job.sh", ["bash", "job.sh"]
    with open(os.path.join(workdir, script), "w", encoding="utf-8") as f:
        f.write(code)

    started = time.time()
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            command, cwd=workdir, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
    if meta["type"] == "background":
        background[job_id] = proc
        time.sleep(3)  # catch scripts that die immediately
        state = "running" if proc.poll() is None else "exited (code %s)" % proc.returncode
        return report(job_id, "background " + state, time.time() - started, log_path,
                      " | pid: %d | log: %s" % (proc.pid, log_path))

    status, last_progress = None, started
    while proc.poll() is None:
        time.sleep(1)
        now = time.time()
        if now - started > float(meta["timeout"]):
            stop(proc)
            status = "timeout"
        elif now - last_progress > PROGRESS_SECONDS:
            last_progress = now
            try:
                if any(n.startswith("cancel_" + job_id) for _, n in store.list_inbox()):
                    stop(proc)
                    status = "cancelled"
                else:
                    store.write("outbox", "progress_%s.txt" % job_id,
                                report(job_id, "running", now - started, log_path))
            except Exception as error:
                print("[worker] progress update failed: %s" % error)
    if status is None:
        status = "ok" if proc.returncode == 0 else "error"
    return report(job_id, status, time.time() - started, log_path,
                  " | exit: %s" % proc.returncode)


def heartbeat(store, done, background):
    gpu = "none"
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip() or "none"
    except Exception:
        pass
    lines = [
        "time_utc: " + time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
        "queue_mode: " + store.name,
        "gpu: " + gpu,
        "jobs_done: %d" % done,
        "background: " + (", ".join(
            "%s=%s" % (k, "running" if p.poll() is None else "exited") for k, p in background.items()
        ) or "none"),
        "comfyui_installed: %s" % os.path.exists("/content/ComfyUI/main.py"),
    ]
    store.write("", "status.txt", "\n".join(lines) + "\n")


def main():
    store = make_store()
    os.makedirs(WORK, exist_ok=True)
    print("[worker] ready (queue mode: %s). Leave this cell running." % store.name)
    seen, background, done, last_beat = set(), {}, 0, 0.0
    while True:
        try:
            if time.time() - last_beat > HEARTBEAT_SECONDS:
                heartbeat(store, done, background)
                last_beat = time.time()
            for handle, name in store.list_inbox():
                if name.startswith("cancel_"):
                    # a cancel only matters while its job runs; anything left here is stale
                    store.finish(handle)
                    continue
                if not name.startswith("job_") or handle in seen:
                    continue
                if name.startswith("job_STOP"):
                    store.finish(handle)
                    print("[worker] stop requested")
                    return
                seen.add(handle)
                print("[worker] running %s" % name)
                job_id = os.path.splitext(name)[0][len("job_"):]
                try:
                    meta, code = parse_job(name, store.read(handle))
                    job_id = str(meta["id"])
                    result = run_job(store, meta, code, background)
                except Exception as error:
                    result = "JOB %s | status: rejected\n--- OUTPUT ---\n%r" % (job_id, error)
                store.write("outbox", "result_%s.txt" % job_id, result)
                store.remove("outbox", "progress_%s.txt" % job_id)
                store.finish(handle)
                done += 1
                last_beat = 0.0
                print("[worker] finished %s: %s" % (name, result.splitlines()[0]))
        except KeyboardInterrupt:
            print("[worker] stopped")
            return
        except Exception as error:
            print("[worker] loop error (will retry): %r" % error)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
