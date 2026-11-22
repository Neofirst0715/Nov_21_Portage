# Portage Co-op Board

加拿大学生 co-op / 实习求职板。一个脚本每小时从雇主的招聘系统读取岗位，写入 `docs/jobs.json`；网站读取这个文件并显示，每条岗位都链接到雇主官网的原始页面。

## 目录

| 路径 | 作用 |
|---|---|
| `scraper/scrape.py` | 抓取脚本（只用 Python 标准库，无需安装依赖） |
| `scraper/sources.json` | 雇主来源列表，加雇主只改这个文件 |
| `docs/index.html` | 网站 |
| `docs/jobs.json` | 岗位数据，由脚本自动生成 |
| `.github/workflows/refresh.yml` | 每小时自动运行脚本的定时配置 |

## 上线步骤（约十分钟）

1. 在 GitHub 新建一个**公开**仓库，例如 `portage-coop-board`（不要勾选自动生成 README）。
2. 在本文件夹打开终端，执行：
   ```bash
   git init -b main
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/<你的用户名>/portage-coop-board.git
   git push -u origin main
   ```
3. 仓库页面 → **Settings → Pages** → Source 选 **Deploy from a branch**，Branch 选 `main`、目录选 `/docs`，保存。
4. 仓库页面 → **Settings → Actions → General → Workflow permissions**，选 **Read and write permissions**，保存。
5. 仓库页面 → **Actions** → 左侧 **Refresh postings** → **Run workflow**。跑完后 `docs/jobs.json` 会有真实数据。
6. 一两分钟后打开 `https://<你的用户名>.github.io/portage-coop-board/`。

之后每小时第 17 分钟自动刷新一次。

## 本地运行

```bash
python3 scraper/scrape.py                     # 抓一次数据
python3 -m http.server --directory docs      # 然后打开 http://localhost:8000
```

直接双击 `index.html` 打不开数据（浏览器不允许本地文件读取 `jobs.json`），要用上面的本地服务器。

## 添加雇主

编辑 `scraper/sources.json`，按雇主用的招聘系统选一种类型：

- **Greenhouse**：招聘页网址形如 `job-boards.greenhouse.io/<board>/...`
  ```json
  {"name": "公司名", "type": "greenhouse", "board": "<board>"}
  ```
- **Lever**：招聘页网址形如 `jobs.lever.co/<site>/...`
  ```json
  {"name": "公司名", "type": "lever", "site": "<site>"}
  ```
- **Workday**：招聘页网址形如 `<tenant>.wd3.myworkdayjobs.com/en-US/<site>/...`
  ```json
  {"name": "公司名", "type": "workday", "host": "<tenant>.wd3.myworkdayjobs.com", "tenant": "<tenant>", "site": "<site>"}
  ```

默认只保留标题里带 co-op / intern / student 且地点在加拿大的岗位。加 `"students_only": false` 可保留该雇主的全部加拿大岗位。

## 现状和待办

已配置的来源：Manulife（Workday）、mthree、Outshine（Greenhouse）。**脚本还没有对真实网站跑过**，第一次运行如果某个来源报错，网站侧栏的 Sources 会显示 failed，把 Actions 日志里的报错发给 Claude 修。

还没接的哈利法克斯主要雇主（它们用的招聘系统不在上面三种里，需要单独写适配）：

- Nova Scotia Power / Emera（`careers.emera.com`）
- 新斯科舍省政府（`jobs.novascotia.ca`）
- RBC（`jobs.rbc.com`）
- Halifax Regional Municipality
- J.D. Irving、BDO、EDC

## 注意

- 这个文件夹在 iCloud Drive 里。iCloud 同步有时会和 git 冲突（出现带 " 2" 的重复文件）。如果遇到，把项目移到非 iCloud 的目录即可。
- 抓取频率是每小时一次，请不要调得更密，避免给雇主网站造成负担。
