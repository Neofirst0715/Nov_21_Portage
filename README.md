# Portage Co-op Board

加拿大学生 co-op / 实习求职板。一个脚本每小时从雇主的招聘系统读取岗位，写入 `docs/jobs.json`；网站读取这个文件并显示，每条岗位都链接到雇主官网的原始页面。

## 目录

| 路径 | 作用 |
|---|---|
| `scraper/scrape.py` | 抓取脚本（只用 Python 标准库，无需安装依赖） |
| `scraper/sources.json` | 雇主来源列表，加雇主只改这个文件 |
| `docs/index.html` | 网站 |
| `docs/jobs.json` | 岗位数据，由脚本自动生成 |
| `setup/refresh.yml` | 刷新任务的配置（手动触发）（上线前要移到 `.github/workflows/`，见下方第 2 步） |

## 上线步骤（约十分钟）

1. 在 GitHub 新建一个**公开**仓库，例如 `portage-coop-board`（不要勾选自动生成 README）。
2. 在本文件夹打开终端，执行：
   ```bash
   mkdir -p .github/workflows && mv setup/refresh.yml .github/workflows/refresh.yml
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

之后想更新时再点一次 Run workflow（或网站右上角的 Refresh postings）。

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

## 聚合来源和自动切换

除了直连雇主，还有三个覆盖面更广的来源：

| 来源 | 需要 key 吗 | 额度 | GitHub secret 名称 |
|---|---|---|---|
| SimplifyJobs | 不需要 | 无限制 | 无 |
| JSearch | 需要 | 免费档每月 200 次请求 | `JSEARCH_API_KEY` |
| Adzuna | 需要 | 未绑卡只有 25 次测试请求 | `ADZUNA_APP_ID`、`ADZUNA_APP_KEY` |

key 存在 GitHub 仓库 → Settings → Secrets and variables → Actions。不要写进任何文件或提交到仓库。

**自动切换**：`sources.json` 里带 `"group": "aggregators"` 的来源互为备用，按文件里从上到下的顺序排优先级。每次刷新只用第一个"有 key 且额度没用完"的，其余待命。排在前面的额度用完（或对方接口返回额度不足）时，自动换下一个，到下个月额度重置后再切回来。SimplifyJobs 没有额度，每次都抓。

网站左侧 Sources 会显示每个来源的状态和剩余请求次数。

**加一个新的聚合来源**：告诉 Claude 是哪家服务，需要给脚本写一个对应的读取函数，然后在 `sources.json` 里加一条，写上 `group` 和 `budget` 即可加入切换。

JSearch 的 `via` 字段：在 OpenWeb Ninja 官网注册的填 `openwebninja`，在 RapidAPI 注册的填 `rapidapi`。

本地运行要带上 key：

```bash
JSEARCH_API_KEY=xxx python3 scraper/scrape.py
```

## 刷新方式

现在是**手动刷新**，没有定时任务：

1. 打开网站，点右上角的 **Refresh postings**（会打开 GitHub 上的 workflow 页面）。
2. 点 **Run workflow** → 绿色的 **Run workflow**。
3. 等大约两分钟（抓取约一分钟，Vercel 重新部署约半分钟），回到网站刷新页面。

每次手动刷新会抓取所有直连来源和 SimplifyJobs，聚合来源按上面的自动切换规则只用一个。JSearch 每次刷新用 8 次请求，Adzuna 用 2 次。

想恢复自动刷新：打开 `.github/workflows/refresh.yml`，把 `schedule` 那两行前面的 `#` 去掉。这时 `sources.json` 里的 `every_hours` 才会生效，用来控制每个来源多久抓一次。

## 现状和待办

已配置的来源：Manulife（Workday）、mthree、Outshine（Greenhouse）、SimplifyJobs、JSearch、Adzuna。**脚本还没有对真实网站跑过**，第一次运行如果某个来源报错，网站侧栏的 Sources 会显示 failed，把 Actions 日志里的报错发给 Claude 修。

还没接的哈利法克斯主要雇主（它们用的招聘系统不在上面三种里，需要单独写适配）：

- Nova Scotia Power / Emera（`careers.emera.com`）
- 新斯科舍省政府（`jobs.novascotia.ca`）
- RBC（`jobs.rbc.com`）
- Halifax Regional Municipality
- J.D. Irving、BDO、EDC

## 注意

- 这个文件夹在 iCloud Drive 里。iCloud 同步有时会和 git 冲突（出现带 " 2" 的重复文件）。如果遇到，把项目移到非 iCloud 的目录即可。
- 如果开启自动刷新，请不要比每小时一次更密，避免给雇主网站造成负担。
