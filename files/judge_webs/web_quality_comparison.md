本次7组样本中，system_c的覆盖度与支撑度均分最高，system_a次之，system_b第三。system_c相对system_a的优势很小：52项的coverage总分仅多1分（71比70），support总分也仅多1分（88比87）。

已完成21份逐项JSON，包含156项判定与351段可定位引文。每份结果保留原始point顺序、coverage、support、引文、来源计数、判定理由和具体缺口。

下表以7个query等权的宏平均为主；括号内为52个point等权的微平均。两项分别评价，不合成总质量分。

| 系统 | 覆盖度 / 2：宏平均（微平均） | 支撑度 / 3：宏平均（微平均） | direct / inferred / none | 空网页样本 |
|---|---:|---:|---|---:|
| system_a | 1.36（1.35） | 1.68（1.67） | 18 / 34 / 0 | 0 |
| system_b | 1.24（1.21） | 1.62（1.58） | 19 / 25 / 8 | 1 |
| system_c | 1.38（1.37） | 1.72（1.69） | 19 / 33 / 0 | 0 |

逐query得分如下。每个单元格为“coverage / support”；点击可查看逐项证据。

| Query | system_a | system_b | system_c |
|---|---:|---:|---:|
| 全球人形机器人量产进展如何，主要厂商的时间表是什么？ | [1.50 / 2.00](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_a_web_quality/system_a_quality_全球人形机器人量产进展如何，主要厂商的时间表是什么？.json>) | [1.50 / 2.00](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_b_web_quality/system_b_quality_全球人形机器人量产进展如何，主要厂商的时间表是什么？.json>) | [1.38 / 1.75](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_c_web_quality/system_c_quality_全球人形机器人量产进展如何，主要厂商的时间表是什么？.json>) |
| 分析近五年全球半导体产业链的关键并购及其影响。 | [1.38 / 1.75](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_a_web_quality/system_a_quality_分析近五年全球半导体产业链的关键并购及其影响。.json>) | [1.38 / 1.75](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_b_web_quality/system_b_quality_分析近五年全球半导体产业链的关键并购及其影响。.json>) | [1.38 / 1.75](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_c_web_quality/system_c_quality_分析近五年全球半导体产业链的关键并购及其影响。.json>) |
| 梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。 | [1.60 / 2.00](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_a_web_quality/system_a_quality_梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。.json>) | [1.60 / 2.20](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_b_web_quality/system_b_quality_梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。.json>) | [1.60 / 2.20](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_c_web_quality/system_c_quality_梳理生成式 AI 在搜索产品中的主要落地方式和代表性产品。.json>) |
| 比较中国主要云计算厂商最新公开的收入、增速和市场定位。 | [1.38 / 1.75](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_a_web_quality/system_a_quality_比较中国主要云计算厂商最新公开的收入、增速和市场定位。.json>) | [1.38 / 1.75](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_b_web_quality/system_b_quality_比较中国主要云计算厂商最新公开的收入、增速和市场定位。.json>) | [1.50 / 1.75](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_c_web_quality/system_c_quality_比较中国主要云计算厂商最新公开的收入、增速和市场定位。.json>) |
| 目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？ | [1.13 / 1.25](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_a_web_quality/system_a_quality_目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？.json>) | [0.00 / 0.00](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_b_web_quality/system_b_quality_目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？.json>) | [1.13 / 1.25](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_c_web_quality/system_c_quality_目前主流大模型长上下文能力的技术路线和公开评测结果有哪些？.json>) |
| 近三年动力电池技术有哪些重要突破，分别由哪些企业推动？ | [1.14 / 1.29](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_a_web_quality/system_a_quality_近三年动力电池技术有哪些重要突破，分别由哪些企业推动？.json>) | [1.43 / 1.86](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_b_web_quality/system_b_quality_近三年动力电池技术有哪些重要突破，分别由哪些企业推动？.json>) | [1.29 / 1.57](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_c_web_quality/system_c_quality_近三年动力电池技术有哪些重要突破，分别由哪些企业推动？.json>) |
| 小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？ | [1.38 / 1.75](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_a_web_quality/system_a_quality_小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？.json>) | [1.38 / 1.75](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_b_web_quality/system_b_quality_小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？.json>) | [1.38 / 1.75](</Users/seinelee/Documents/my_search_agent/files/judge_webs/system_c_web_quality/system_c_quality_小米发布第二款车之后的下一个大型车展上，其竞品都发布了什么车？.json>) |

差异主要集中在以下内容：

- 人形机器人：system_a与system_b对样机、试产、实际产出和计划产能的区分更充分；三者均缺逐主要厂商、同一状态时点的完整进度表。
- 半导体并购：三者最终档位相同，但具体证据不同。例如system_b保存了新思完成收购Ansys的报道，system_c相关材料仍为批准后预计交割。由于“交易事实与进度”要求逐笔齐备，单笔更完整尚不足以使整项升为direct。
- 生成式AI搜索：三者均存在分类或案例正文截断。system_b、system_c提供了更多独立来源的具体使用限制，支撑度略高。
- 中国云厂商：system_c对内外部收入、重述基期和混合分部的差异交代更充分，覆盖度更高。最后复核将“华为自身收入下降”判为弱支撑：自身同比下降不能单独证明跨厂商比较中的不占优。
- 长上下文：system_b的指定webs为空，8项全部计0，未从其他轨迹字段补证。system_a有MRCR与捞针分数，system_c有LongBench v2模型成绩；两者均缺完整测试长度、配置及逐模型条件，实际成绩项仍为inferred。
- 动力电池：system_b在重要性依据与技术限制方面更充分，system_c次之；三者仍缺各项突破统一比较基准、验证条件及逐项企业贡献。
- 小米车展：三者有车型身份、发布和展会时间材料，但尚未闭合“下一场大型车展”的筛选，也未把最终竞品范围与发布清单逐成员对应。后续项保留局部证据，未按依赖关系直接清零。

system_b的空结果对整体排名影响较大。为检验这一点，三个系统同时剔除长上下文一组，剩余6个query按相同样本配对重新计算：

| 系统 | 覆盖度宏平均 / 2 | 支撑度宏平均 / 3 |
|---|---:|---:|
| system_a | 1.39 | 1.76 |
| system_b | 1.44 | 1.88 |
| system_c | 1.42 | 1.80 |

这6组中system_b两项均领先；包含空结果的7组交付评价仍以system_c最高。因此，本批完整交付表现可选system_c作为暂时领先者；system_b应优先排查长上下文样本为何未交付网页。此处只观察到空数组，没有据此推断故障原因。

评估口径与复核记录：

- 只使用指定webs实际保存的正文，未访问链接补齐、使用已有答案或引入外部事实。支持的是已保存表述的完整性与来源数量，不额外判断网页陈述在现实中是否正确。
- 每个point按完整要求评分，只有明确要求逐类、逐成员的要点才作完整集合检查。局部支撑计inferred不代表其余缺失事实可以推导出来。不同系统的局部材料丰富程度可能不同，但仍落在同一档位。
- 引文逐字连续匹配原始content或web_content；同域多篇合并，重复稿件不增加独立来源数。所有support=3项均核查来源数。
- 156项全部纳入，包含should项及未覆盖项，不沿用points文件中的闸门停分规则。宏平均先用未舍入的逐query分数计算，再四舍五入；微平均保留总分及52项分母。
- 相对时间以各轨迹运行日为参照：system_a为2026-08-24；system_b为2026-09-04；system_c为2026-08-24，小米一组为2026-08-26。它们不是同一时点的受控实验。每组每系统仅一个样本，不能把小幅分差视为稳定优势或统计显著结果。

[机器可读汇总与全部输入输出路径](</Users/seinelee/Documents/my_search_agent/files/judge_webs/web_quality_comparison.json>)
[评估prompt](</Users/seinelee/Documents/my_search_agent/files/judge_webs/web_quality_prompt>)
