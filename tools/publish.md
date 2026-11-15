# 发布到 GitHub（一键备好，就差仓库与授权）

本地仓库已就绪：`E:\AI_Projects\Zcode\quantforge`，提交 `e412452` 等。

## 方式 A（推荐，1 分钟）：网页建仓 → 弹窗授权

1. 登录 github.com → **New repository** → 名字建议 `quantforge`（公开/Private 任选，开源建议 Public）→ Create；
2. 复制新仓库地址（https://github.com/<你>/quantforge.git），在本目录执行：

```powershell
git remote add origin https://github.com/<你>/quantforge.git
git push -u origin main
```

3. 第一次推送会弹出 **Git Credential Manager 窗口**——点允许（浏览器登录一次），以后免交互。

## 方式 B：给我一个 PAT

在 Settings → Developer settings → Personal access tokens（经典，repo 权限）生成后，
把 token 发我，我用 API 建仓并推送（全程免你操作）。

## 推送后

- GitHub 会自动跑 `.github/workflows/ci.yml`（Python 3.10/3.13 × Ubuntu/Windows 全量测试 + gate）；
- 仓库里不含真实行情数据（`data/` 已 gitignore，D-010 红线），任何人 clone 后
  `python qf.py demo` 即可离线出报告（已做全新克隆复现验证）。
