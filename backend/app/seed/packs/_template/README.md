# `_template` —— 世界包模板

复制这个目录，改名成你的 `<world_key>`，然后按
[世界包规范](../../../../docs/世界包规范.md) 改写两个 JSON。

**注意**

- `world.json` 的 `key` 必须与目录名一致；
- 两个文件的 `horizon_months` 必须相同；
- `rubric.json` 里 `metrics` 的 `weight` 之和必须是 1.0；
- 引用的 region / faction / character key 必须真实存在。

改完跑：

```powershell
python tools\check_world_pack.py <world_key>
```

`seed_module` 留空表示内容由外部提供（v2 会把内容也放进包里）。