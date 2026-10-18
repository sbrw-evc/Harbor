# SonarQube

Every release is analysed by [SonarQube](http://192.168.0.104:9000/dashboard?id=monitoring-harbor) (project `monitoring-harbor`) before its images are built; a release whose quality gate fails is not published.

| Release | Date | Quality gate | Bugs | Vulnerabilities | Code smells | Coverage | Duplications | Commit |
|---|---|---|---|---|---|---|---|---|
| [v2.0.4](SonarQube-v2.0.4.md) | 2026-10-18 | Passed | 32 | 13 | 95 | 28.4% | 0.3% | [`e312a10`](http://192.168.0.104:9000/dashboard?id=monitoring-harbor) |
