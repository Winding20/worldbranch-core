# 让 AI 制作一个新世界的操作手册

> 这份文件是给 AI 看的。人看 [世界包规范](世界包规范.md) 更详细，但流程一样。
> 目标：只说一句「把背景设在某段历史」，就能产出一个能跑的世界包。

---

## 一、先问清楚（缺什么就问，不要猜）

1. **时段**：从哪年哪月到哪年哪月？（决定 `start_date` 与 `horizon_months`）
2. **玩家扮演谁**：哪个政权/势力？（决定 `role`、`player_faction`）
3. **对手是谁**：主要的敌对势力是哪个？（决定 `hostile_faction`）
4. **成败看什么**：这段历史里，什么样算赢？（决定 rubric 的指标与权重）

这四件事定了，剩下全是数据填空题。

---

## 二、复制模板

```powershell
Copy-Item backend\app\seed\packs\_template backend\app\seed\packs\<world_key> -Recurse
```

`<world_key>` 用**小写字母+数字+下划线**，例如 `sanguo_208`、`tang_755`。
目录名必须等于 `world.json` 里的 `key`。

---

## 三、按顺序写四个文件

### 1. `world.json`（先写这个）

```json
{
  "key": "<world_key>",
  "title": "界面上显示的大标题",
  "subtitle": "副标题（时段之类）",
  "start_date": "0208-07-01",
  "horizon_months": 12,
  "role": "玩家扮演的势力",
  "role_brief": "一句话说明玩家处境",
  "theme": "celadon-paper",
  "player_faction": "liu",
  "hostile_faction": "cao",
  "labels": { "advance_unit": "月", "horizon_label": "一年之后" }
}
```

**年份写四位**：208 年要写 `0208`。

### 2. `content.json`（世界的内容）

七块：`world`（名称/时代/简介/史料出处）、`regions`、`factions`、
`relations`、`trade_pairs`、`characters`、`armies`、`events`。

- 地区只要有 `key`、`name`、`lat`、`lon`、`population`、`faction_key`；
  耕地与存粮会自动按人口推算，不用填；
- 边界可以不给（不写 `geo`），界面会用经纬度画点；
- 人物要 `key`、`name`、`role`、`faction_key`、`ability`(0–1)、
  `loyalty`(0–1)、`ambition`(0–1)、`location_region_key`；
- 军队要 `key`、`name`、`faction_key`、`commander_key`、`region_key`、`troops`；
- 史实要 `key`、`date`、`title`、`description`、`conditions`、`effects`、
  `divergence_note`（前提不成立时的一句解释）、`source`。

**引用一律用 key**，别写中文名。

### 3. `rubric.json`（怎么算赢）

先回答：这段历史的成败是哪几件事？各占多重？权重之和必须是 1.0。

```json
{
  "horizon_months": 12,
  "grades": [[78, "鼎足", "一句评语"], [0, "覆没", "一句评语"]],
  "metrics": [
    { "key": "foothold", "label": "立足", "weight": 0.5,
      "kind": "region_control", "regions": ["jiangxia"], "faction": "liu" }
  ]
}
```

指标类型见规范 3.2；刻意让「最难的那个目标」权重最高。

### 4. `decisions.json`（玩家会被问什么）

只写这段历史里**真正会纠结的选择**。两个例子：

```json
{
  "rules": [
    {
      "kind": "alliance_choice",
      "when": { "region_held_by": { "region": "jiangxia", "faction": "liu" } },
      "focus": { "region": "jiangxia" },
      "title": "鲁肃来见",
      "description": "联吴则共抗曹操，不联则西走。",
      "options": [
        { "key": "ally", "label": "联吴抗曹", "detail": "威望上升",
          "effects": { "relation": { "sun": 0.25 }, "legitimacy": 0.05 } }
      ]
    }
  ]
}
```

要按人口算钱就写表达式：

```json
"vars": { "cost": "round(focus_population * 0.5)" },
"effects": { "treasury": "-cost" }
```

要「每支没主帅的军队都问一次」：

```json
"each": { "entity": "army", "where": { "faction": "player", "no_living_commander": true } },
"ref_title": "{each}无主帅",
"options_from": "appoint_commander"
```

---

## 四、校验（必须做）

```powershell
python tools\check_world_pack.py <world_key>
```

它会告诉你：字段缺什么、权重加不加得到 1、引用的地区/派系/人物 key 是否存在、
条件名和效果名是否拼错。**任何一条不过就退回改 JSON，不要开始跑。**

---

## 五、跑起来看

```powershell
python tools\bootstrap.py     # 首次
start.bat                     # 或 python backend\serve.py
```

浏览器打开 `http://127.0.0.1:5000/?world=<world_key>`，
或在界面左上角的世界下拉里切换。

检查三件事：

1. 标题、角色、期限对不对；
2. 推进几个月，史实有没有按期发生；
3. 到期末，结局评分是否符合你的历史判断。

---

## 六、常见错误对照表

| 现象 | 原因 |
|---|---|
| 报「缺少文件」 | 少了 `world.json` 或 `rubric.json` |
| 报「key 与目录名不一致」 | `world.json` 的 `key` 和目录名不同 |
| 报「权重之和不是 1.0」 | rubric 权重没配平 |
| 启动后是空白地图 | 没给 `geo`，属正常——会画点；给了坐标但没给点，检查 `lat/lon` |
| 某个决策从不出现 | `when` 太严，或 `focus` 指的地区不在本世界 |
| 决策出现了但选项没变化 | 效果名写错（引擎会报未知效果），或作用对象 key 不存在 |
| 日期报错 | 年份没写四位，或写了 `208-07-01` 而不是 `0208-07-01` |

---

## 七、交活标准

- `python tools\check_world_pack.py <world_key>` 通过；
- 能玩满一个期限并拿到结局；
- 至少 2 件史实、2 个决策；
- 史料出处写在 `events[].source`，估算值不冒充原文。

满足这四条，别人拿到你的包就能直接跑。