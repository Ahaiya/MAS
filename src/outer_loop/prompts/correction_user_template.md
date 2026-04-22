## 当前 task_context.yaml 内容

```yaml
{{ current_task_context }}
```

## 教师批改意见（共 {{ total_events }} 条）

{% for event in correction_events %}
### 样本：{{ event.sample_id }}（{{ event.timestamp }}）

{% if event.score_corrections %}
**分数修改：**
{% for c in event.score_corrections %}
- 维度 {{ c.dimension_code }}：AI 给 {{ c.original_score }} 分 → 教师改为 {{ c.corrected_score }} 分（{% if c.corrected_score > c.original_score %}AI 偏低{% else %}AI 偏高{% endif %}）
{% endfor %}
{% endif %}

{% if event.feedback_corrections %}
**反馈文本修改：**
{% for c in event.feedback_corrections %}
- 维度 {{ c.dimension_code }}
  - 原始反馈：{{ c.original_feedback }}
  - 教师改写：{{ c.corrected_feedback }}
{% endfor %}
{% endif %}

{% if event.evidence_additions %}
**新增证据：**
{% for c in event.evidence_additions %}
- 维度 {{ c.dimension_code }}，教师新增了以下引用：
{% for q in c.added_quotes %}
  - "{{ q }}"
{% endfor %}
{% endfor %}
{% endif %}

{% endfor %}

---

请根据以上批改意见，输出更新后的完整 task_context.yaml 内容。
