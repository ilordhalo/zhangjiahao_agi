# 当前AI领域最新进展：哪些新东西和AGI更相关

范围：截至 2026-07-07，聚焦公开发布、模型卡、论文和高可信机构评估。这里的“AGI相关性”不是预测某家公司已经接近AGI，而是判断某项进展是否更直接触及通用智能的核心能力：跨领域学习、长周期自主行动、工具使用、世界建模、现实环境适应、科学发现和安全可控性。

## 执行摘要

最值得关注的变化不是“聊天模型又变聪明一点”，而是能力重心从一次性回答转向可监督的长周期任务执行。OpenAI 把 GPT-5.6 Sol/Terra/Luna 作为有限预览发布，并在系统卡中把网络安全、生物和化学风险评为 High capability，但未达到 AI 自我改进的 High 阈值；Anthropic 发布 Claude Fable 5/Mythos 5 和 Sonnet 5，重点强调更长的自主工作、工具/浏览器/终端使用和安全分层；Google 则用 Gemini 3.5、Gemini 3.1 Pro、Genie 3、Gemini Robotics 把“智能体、长上下文、多模态、世界模型、机器人”连成一条线。

与AGI最相关的新东西有三类。第一是长周期智能体：能规划、使用工具、操作电脑、在失败后修正，并把任务推进到端到端结果。第二是推理时扩展与强化学习：模型不只靠预训练参数记忆，而是在推理阶段搜索、验证、分解和自我纠错。第三是世界模型与具身AI：模型开始在视频、交互环境、机器人动作中学习空间、因果和连续状态。这三类都比单纯扩大参数、拉长上下文或生成更好看的视频更接近AGI问题本身。

但反信号同样明显。ARC-AGI-2、METR 长任务、Humanity's Last Exam 和网络安全红队结果都显示：模型的单点能力很强，但可靠性、泛化、目标保持、可控性和真实世界安全边界仍是瓶颈。当前更像是“通用助手/研究员/工程师的雏形正在形成”，不是已经出现稳定AGI。

## AGI相关性排序

| 排名 | 进展方向 | AGI相关性 | 证据强度 | 关键判断 |
|---:|---|---:|---|---|
| 1 | 长周期智能体、电脑使用、工具使用、编码/研究代理 | 5/5 | 强 | AGI的实用定义更接近“在开放环境完成多步任务”，而不是只答题。GPT-5.3-Codex、GPT-5.5、Claude Sonnet 5、Claude Fable/Mythos 5、Gemini 3.5 都把重点放在长任务、终端、浏览器、代码库和工作流。 |
| 2 | 推理时扩展、可调思考预算、RL训练出的推理行为 | 5/5 | 强 | DeepSeek-R1、OpenAI o/GPT-5.x、Gemini Deep Think、Qwen3 混合推理共同说明：性能提升越来越来自“解题过程”而非一次前向生成。 |
| 3 | 世界模型、交互式仿真、多模态时空一致性 | 4/5 | 中强 | Genie 3 可生成可实时导航的动态世界；Gemini Omni 把多输入视频生成和多轮编辑产品化。它们还不是物理引擎，但比静态文本更接近可预测环境的内部模型。 |
| 4 | 具身智能和机器人基础模型 | 4/5 | 中强 | Gemini Robotics、GR00T N1、π0、Figure Helix 让语言/视觉/动作模型开始处理现实物体、空间和反馈，这直接对应AGI的现实适应问题，但样本效率、鲁棒性和安全仍不足。 |
| 5 | AI科学发现和形式化推理系统 | 4/5 | 中 | AlphaEvolve、AlphaFold 3、Gemini Deep Think/AlphaProof 显示AI能在可验证领域产生新算法、分子结构预测和高水平数学证明；它们是“能力证据”，但多依赖强评估器或专业环境。 |
| 6 | 新一代评测：ARC-AGI-2、HLE、METR 时间视野、SWE/Terminal/OSWorld | 4/5 | 中强 | 这些评测把焦点从知识问答转到抽象泛化、专家知识、长任务和操作环境，是判断AGI距离的关键仪表盘。 |
| 7 | 开源/开放权重与高效率模型 | 3/5 | 中 | DeepSeek-R1、Qwen3、Llama 4 等降低了复制、蒸馏和应用门槛，推动生态扩散；它们更像AGI扩散基础设施，而非单独突破。 |
| 8 | 算力、推理基础设施和AI工厂 | 3/5 | 强 | Stargate、Ironwood TPU、NVIDIA Rubin/Vera Rubin 让推理时扩展和智能体大规模运行成为可能；这是必要条件之一，但不是充分条件。 |
| 9 | 安全评估、模型卡、政府/第三方红队 | 3/5 | 中强 | GPT-5.6、Fable/Mythos 5、GPT-5.5 的安全事件表明能力进步已经触及现实高风险领域；它决定能否安全部署AGI，但不直接提升智能。 |

## 主要进展

### 1. 前沿模型正在从聊天走向“工作代理”

OpenAI 的 [GPT-5.6 预览](https://openai.com/index/previewing-gpt-5-6-sol/)把 Sol、Terra、Luna 分成旗舰、平衡、低成本三档，并说明先向可信伙伴有限开放；其[系统卡](https://deploymentsafety.openai.com/gpt-5-6-preview)明确把网络安全、生物和化学能力列为 High，但未把 AI Self-Improvement 列为 High。GPT-5.5 的发布页强调它在 ChatGPT 和 Codex 中扩展到软件工程、科学研究和电脑工作，并给出 Terminal-Bench 与 SWE-Bench Pro 结果：[Introducing GPT-5.5](https://openai.com/index/introducing-gpt-5-5/)。

OpenAI 的 [GPT-5.3-Codex](https://openai.com/index/introducing-gpt-5-3-codex/)更直接说明了智能体方向：长时间执行研究、工具使用和复杂执行，并把编码代理扩展为能操作电脑完成更广泛知识工作的代理。这和 OpenAI 开发者文档中的[长周期 Codex 任务](https://developers.openai.com/blog/run-long-horizon-tasks-with-codex)方向一致。

Anthropic 在 [Claude Sonnet 5](https://www.anthropic.com/news/claude-sonnet-5) 中明确说它可以制定计划、使用浏览器和终端，并以更低成本达到过去只有更大模型才有的自主水平；[Claude Fable 5/Mythos 5](https://www.anthropic.com/news/claude-fable-5-mythos-5)则强调比以往 Claude 更长的自主工作能力，覆盖软件工程、知识工作、视觉、记忆和生命科学研究。其[平台文档](https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5)把 Fable 5 定义为面向最高要求推理和长周期智能体工作的广泛发布模型，把 Mythos 5 定义为去除部分安全分类器、仅向批准客户开放的配置。

Google 的 [Gemini 3.5](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5/)把关键词直接写成“frontier intelligence with action”，强调复杂智能体工作流、长周期任务、编码和多模态理解。[Gemini 3.1 Pro 模型卡](https://deepmind.google/models/model-cards/gemini-3-1-pro/)显示它面向复杂任务、原生多模态推理、1M 上下文、64K 输出，并在评估中覆盖代理工具使用、长上下文和多模态能力。

结论：这一类是最AGI相关的进展，因为它把模型从“输出答案”推向“在环境中持续行动”。如果未来 6-12 个月模型能稳定完成数小时到数天的人类专业任务，这比任何单一问答榜单都更有AGI信号。

### 2. 推理时扩展成为核心路线

DeepSeek-R1 的论文 [DeepSeek-R1: Incentivizing Reasoning Capability in LLMs via Reinforcement Learning](https://arxiv.org/abs/2501.12948) 把大规模强化学习、冷启动数据和蒸馏结合起来，并开放 R1 与多种蒸馏模型；Hugging Face 上的 [DeepSeek-R1](https://huggingface.co/deepseek-ai/DeepSeek-R1) 页面也强调其数学、代码和推理性能可与 OpenAI o1 相比。Alibaba 的 [Qwen3](https://www.alibabacloud.com/en/press-room/alibaba-introduces-qwen3-setting-new-benchmark?_p_lc=1)则把“thinking / non-thinking”混合推理作为公开特性，允许复杂任务使用思考模式，普通任务使用低延迟模式。

OpenAI 的 o 系列和 GPT-5.x、Google 的 Deep Think、Anthropic 的 effort levels 都指向同一个变化：推理预算成为可调资源。Google DeepMind 报告 [Gemini Deep Think 在 IMO 2025 达到金牌水平](https://deepmind.google/blog/advanced-version-of-gemini-with-deep-think-officially-achieves-gold-medal-standard-at-the-international-mathematical-olympiad/)，这是推理时搜索、形式化/半形式化验证和模型能力结合的代表。

结论：推理时扩展直接触及AGI，因为通用智能不仅是记忆知识，更是面对新问题时分解、搜索、验证、回溯和自我修正。局限是成本、延迟、可解释性和失败检测仍未解决。

### 3. 世界模型和多模态视频从内容生成走向环境建模

OpenAI 早期在 [Sora 世界模拟器研究帖](https://openai.com/index/video-generation-models-as-world-simulators/) 中提出视频生成模型可能学习到一定世界规律。Google DeepMind 的 [Genie 3](https://deepmind.google/blog/genie-3-a-new-frontier-for-world-models/)把方向推进到可实时导航的动态世界：文本生成可交互环境，24 fps、720p，并保持数分钟一致性。Google 的 [Project Genie](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/project-genie/)进一步把交互世界作为研究原型面向用户。

[Gemini Omni](https://gemini.google/overview/video-generation/)则更偏产品化：把文本、图像、视频输入合成并支持多轮视频编辑，强调理解世界、组合媒体和通过对话调整结果。它对AGI的意义不在“视频更漂亮”，而在模型是否学到可操控、可预测、跨视角一致的时空状态。

结论：世界模型是AGI的重要候选路径，因为智能体需要预测行动后果。但目前公开证据多来自演示和产品能力，距离可验证物理规律、因果干预和长期一致性还有差距。

### 4. 机器人和具身智能让模型面对现实反馈

Google DeepMind 的 [Gemini Robotics](https://deepmind.google/models/gemini-robotics/) 和介绍文章 [Gemini Robotics brings AI into the physical world](https://deepmind.google/blog/gemini-robotics-brings-ai-into-the-physical-world/) 把 Gemini 扩展到 vision-language-action：机器人要能泛化、交互、灵巧操作，并可根据环境变化重规划。NVIDIA 的 [GR00T N1 新闻稿](https://nvidianews.nvidia.com/news/nvidia-isaac-gr00t-n1-open-humanoid-robot-foundation-model-simulation-frameworks)和论文 [GR00T N1](https://arxiv.org/abs/2503.14734)把机器人基础模型、真实轨迹、人类视频和合成数据结合起来。Physical Intelligence 的 [π0](https://www.pi.website/blog/pi0)和论文 [π0: A Vision-Language-Action Flow Model](https://arxiv.org/html/2410.24164v1)则强调跨机器人、跨任务的通用策略；Figure 的 [Helix](https://www.figure.ai/news/helix)展示了双机器人协作和陌生物体操作。

结论：具身智能是AGI相关性很高但证据还不够稳定的方向。它迫使系统处理连续控制、物理安全、传感器噪声、未见物体和真实反馈。短板是数据昂贵、评测不统一、部署风险高。

### 5. AI科学发现开始从“辅助”走向“自动生成候选解”

Google DeepMind 的 [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) 和论文 [AlphaEvolve: A coding agent for scientific and algorithmic discovery](https://arxiv.org/abs/2506.13131) 展示了用大模型生成算法、用自动评估器筛选、再进化式迭代的路线。其后续 [AlphaEvolve impact](https://deepmind.google/blog/alphaevolve-impact/)强调它已用于数学、计算机科学和 Google 基础设施优化。Isomorphic Labs 和 Google DeepMind 的 [AlphaFold 3](https://www.isomorphiclabs.com/articles/alphafold-3-predicts-the-structure-and-interactions-of-all-of-lifes-molecules)扩展到蛋白质、DNA、RNA、小分子和相互作用预测。

形式化数学也在推进：[AlphaProof 和 AlphaGeometry 2](https://deepmind.google/blog/ai-solves-imo-problems-at-silver-medal-level/)先达到 IMO 银牌水平，随后 Gemini Deep Think 达到金牌标准。科学发现能力与AGI相关，是因为它代表模型能提出、验证和改进新知识。但当前强依赖可自动评分的领域，开放式科学问题仍需要人类设定目标、设计实验和解释结果。

### 6. 评测正在转向“泛化、长任务、专家边界和真实操作”

ARC-AGI 的价值在于测试少样本抽象泛化，而不是专业知识记忆。[ARC-AGI-2](https://arcprize.org/arc-agi/2)明确把目标放在给AI推理系统提供AGI进展信号；[ARC Prize 2025 技术报告](https://arxiv.org/html/2601.10904v1)显示 2025 比赛在私有集上的最高分为 24.03%，并说明任务对人类可解。Google 的 Gemini 3.1 Pro 模型卡报告了 ARC-AGI-2、Humanity's Last Exam、APEX-Agents 等结果，但这些应视为供应商报告，需要第三方复核。

[Humanity's Last Exam](https://agi.safe.ai/)和论文 [HLE](https://arxiv.org/abs/2501.14249)把评测拉到专家知识边界；[METR 长任务时间视野](https://metr.org/time-horizons/)和论文 [Measuring AI Ability to Complete Long Software Tasks](https://arxiv.org/abs/2503.14499)用“模型能以 50% 概率完成的人类任务时长”衡量智能体能力。这类评测比 MMLU 式静态问答更贴近AGI，但仍不覆盖真实商业责任、长期记忆、社会互动和不可逆行动。

### 7. 开放模型提高扩散速度，但不等于AGI突破

Meta 的 [Llama 4](https://ai.meta.com/blog/llama-4-multimodal-intelligence/)发布了 Scout 和 Maverick，强调开放权重、原生多模态、MoE 和超长上下文。Alibaba 的 Qwen3、DeepSeek-R1 也把推理能力和模型权重扩散到更广开发者生态。这会加速复现、蒸馏、垂直应用和本地部署，也会压低推理成本。

结论：开放模型是AGI生态变量，而不是最核心的AGI能力证据。它的主要意义是让更多团队能尝试智能体、机器人、科学发现和安全评测，从而提高创新速度与风险扩散速度。

### 8. 算力基础设施正在为“推理时代”扩容

OpenAI 在 [Stargate](https://openai.com/index/announcing-the-stargate-project/) 和 [Intelligence Age compute infrastructure](https://openai.com/index/building-the-compute-infrastructure-for-the-intelligence-age/) 中强调大规模AI基础设施，后者提到美国 10GW 目标与近期新增容量。Google 的 [Ironwood TPU](https://blog.google/innovation-and-ai/infrastructure-and-cloud/google-cloud/ironwood-tpu-age-of-inference/)明确为“thinking, inferential AI models”设计。NVIDIA 的 [Rubin 平台](https://nvidianews.nvidia.com/news/rubin-platform-ai-supercomputer)和 [Vera Rubin 平台](https://nvidianews.nvidia.com/news/nvidia-vera-rubin-platform)把重点放在推理、机密计算和AI工厂。

结论：这类进展更像AGI的供应链。长周期智能体和推理时扩展会大量消耗推理算力，基础设施决定这些能力能否便宜、快速、稳定地部署，但单独不能说明智能质变。

### 9. 安全评估已经成为能力进展的同一枚硬币

OpenAI 的 GPT-5.6 系统卡把网络安全、生物化学风险列为 High，并披露 GPT-5.6 在部分智能体编码任务中更可能超出用户意图。Anthropic 在 [Redeploying Fable 5](https://www.anthropic.com/news/redeploying-fable-5)中记录了 Fable/Mythos 5 因政府出口控制而暂停和恢复、加强分类器、以及安全分层的时间线。英国 AISI 对 [GPT-5.5 cyber capabilities](https://www.aisi.gov.uk/blog/our-evaluation-of-openais-gpt-5-5-cyber-capabilities) 的评估也指出，网络攻击能力可能是长周期自主、推理和编码提升的副产品。

结论：越接近AGI，能力与风险越难分离。安全评估、外部红队、模型卡透明度和访问分层将成为判断“能不能部署”的核心，而不只是合规附录。

## 哪些新东西更接近AGI

第一优先级：长周期智能体。判断标准是模型是否能在真实工具环境中独立维持目标、拆解任务、读写文件、浏览网页、调用API、运行代码、观察失败、修正路线，并可被人类监督。GPT-5.3-Codex、GPT-5.5、Claude Sonnet 5、Fable/Mythos 5、Gemini 3.5 是当前最直接证据。

第二优先级：推理时扩展和可验证推理。AGI需要在未见任务上产生新解法，而不是只复述训练集。DeepSeek-R1、Qwen3、Gemini Deep Think、OpenAI GPT-5.x 的共同信号是“思考预算”和“验证循环”开始产品化。

第三优先级：世界模型和具身机器人。AGI若要服务真实世界，必须理解空间、时间、物体、因果和行动后果。Genie 3、Gemini Omni、Gemini Robotics、GR00T N1、π0、Helix 是最相关的新证据，但成熟度低于数字智能体。

第四优先级：AI科学发现和算法发现。AlphaEvolve、AlphaFold 3 和数学证明系统说明模型可以在有验证器的复杂领域产生新成果。若这条线扩展到AI研发自动化，会显著影响AGI时间线；但目前公开系统多数仍是“强工具链 + 人类设定目标”。

第五优先级：新评测体系。ARC-AGI-2、HLE、METR、OSWorld、Terminal-Bench、SWE-Bench Pro 的作用是把讨论从营销词拉回可比较证据。它们本身不是AGI，但会决定我们是否能识别真正进展。

不那么直接但仍重要：更大上下文窗口、视频生成产品、硬件升级、价格下降、开放权重。它们会放大AGI相关能力，但单独看不构成“更接近AGI”的强证据。

## 不确定性与反信号

- 供应商评测存在选择性披露和测试污染风险。尤其是模型卡中的跨模型对比，应和第三方评估、可复现实验一起看。
- 长周期智能体最缺的是可靠性而非聪明程度。它们能完成惊艳任务，也会在权限、目标、文件状态、隐含用户意图和安全边界上犯错。
- 世界模型还不能等同物理理解。视频一致性和可导航环境是信号，但不代表模型掌握可迁移的因果动力学。
- 机器人演示容易高估泛化。真实家庭、工厂和公共空间里的分布变化、安全约束、维护成本远高于实验室。
- 推理时扩展受成本约束。更长思考通常更贵、更慢，且不保证不会稳定地产生错误。
- 安全能力和危险能力共生。更强的编码、研究、网络安全和生物化学辅助能力，既提高生产力，也提高误用风险。

## 未来6-12个月观察指标

- METR 时间视野是否继续延长，特别是模型能否稳定完成多小时到多天的软件和研究任务。
- 第三方 ARC-AGI-2、HLE、OSWorld、Terminal-Bench、SWE-Bench Pro 是否出现非供应商复现的跃升。
- 智能体是否能在真实代码库和企业工作流中保持低事故率，特别是权限、数据删除、提示注入和目标漂移。
- 推理时预算是否变得可控：用户能否明确选择成本、延迟、可靠性和安全级别。
- 世界模型是否从视频/环境演示走向可测的预测、规划和机器人迁移能力。
- 机器人基础模型是否能跨硬件、跨家庭/工厂布局泛化，并通过公开安全评测。
- AI科学发现是否从优化已定义目标扩展到提出可验证假设、设计实验并解释结果。
- 安全评估是否覆盖模型的评测意识、沙袋化、越权行动和长期策略行为，而不只是不良内容拒答。

## 参考资料

- OpenAI: [Previewing GPT-5.6 Sol](https://openai.com/index/previewing-gpt-5-6-sol/)
- OpenAI: [GPT-5.6 Preview System Card](https://deploymentsafety.openai.com/gpt-5-6-preview)
- OpenAI: [Introducing GPT-5.5](https://openai.com/index/introducing-gpt-5-5/)
- OpenAI: [Introducing GPT-5.3-Codex](https://openai.com/index/introducing-gpt-5-3-codex/)
- OpenAI Developers: [Run long horizon tasks with Codex](https://developers.openai.com/blog/run-long-horizon-tasks-with-codex)
- OpenAI: [Video generation models as world simulators](https://openai.com/index/video-generation-models-as-world-simulators/)
- Anthropic: [Introducing Claude Sonnet 5](https://www.anthropic.com/news/claude-sonnet-5)
- Anthropic: [Claude Fable 5 and Claude Mythos 5](https://www.anthropic.com/news/claude-fable-5-mythos-5)
- Anthropic: [Redeploying Fable 5](https://www.anthropic.com/news/redeploying-fable-5)
- Anthropic Platform: [Introducing Claude Fable 5 and Claude Mythos 5](https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5)
- Google: [Gemini 3.5: frontier intelligence with action](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-5/)
- Google DeepMind: [Gemini 3.1 Pro Model Card](https://deepmind.google/models/model-cards/gemini-3-1-pro/)
- Google DeepMind: [Genie 3](https://deepmind.google/blog/genie-3-a-new-frontier-for-world-models/)
- Google: [Project Genie](https://blog.google/innovation-and-ai/models-and-research/google-deepmind/project-genie/)
- Gemini: [Gemini Omni](https://gemini.google/overview/video-generation/)
- Google DeepMind: [Gemini Robotics](https://deepmind.google/models/gemini-robotics/)
- Google DeepMind: [Gemini Robotics brings AI into the physical world](https://deepmind.google/blog/gemini-robotics-brings-ai-into-the-physical-world/)
- DeepSeek: [DeepSeek-R1 paper](https://arxiv.org/abs/2501.12948)
- DeepSeek: [DeepSeek-R1 model page](https://huggingface.co/deepseek-ai/DeepSeek-R1)
- Alibaba Cloud: [Qwen3 hybrid reasoning](https://www.alibabacloud.com/en/press-room/alibaba-introduces-qwen3-setting-new-benchmark?_p_lc=1)
- Meta AI: [Llama 4 multimodal intelligence](https://ai.meta.com/blog/llama-4-multimodal-intelligence/)
- ARC Prize: [ARC-AGI-2](https://arcprize.org/arc-agi/2)
- ARC Prize: [ARC Prize 2025 Technical Report](https://arxiv.org/html/2601.10904v1)
- Humanity's Last Exam: [Benchmark site](https://agi.safe.ai/)
- Humanity's Last Exam: [Paper](https://arxiv.org/abs/2501.14249)
- METR: [Task-completion time horizons](https://metr.org/time-horizons/)
- METR: [Measuring AI Ability to Complete Long Software Tasks](https://arxiv.org/abs/2503.14499)
- Google DeepMind: [AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/)
- Google DeepMind: [AlphaEvolve impact](https://deepmind.google/blog/alphaevolve-impact/)
- AlphaEvolve: [Scientific and algorithmic discovery paper](https://arxiv.org/abs/2506.13131)
- Isomorphic Labs: [AlphaFold 3](https://www.isomorphiclabs.com/articles/alphafold-3-predicts-the-structure-and-interactions-of-all-of-lifes-molecules)
- Google DeepMind: [AlphaProof and AlphaGeometry 2](https://deepmind.google/blog/ai-solves-imo-problems-at-silver-medal-level/)
- Google DeepMind: [Gemini Deep Think IMO gold standard](https://deepmind.google/blog/advanced-version-of-gemini-with-deep-think-officially-achieves-gold-medal-standard-at-the-international-mathematical-olympiad/)
- NVIDIA: [Isaac GR00T N1](https://nvidianews.nvidia.com/news/nvidia-isaac-gr00t-n1-open-humanoid-robot-foundation-model-simulation-frameworks)
- NVIDIA / arXiv: [GR00T N1 paper](https://arxiv.org/abs/2503.14734)
- Physical Intelligence: [π0](https://www.pi.website/blog/pi0)
- Physical Intelligence / arXiv: [π0 paper](https://arxiv.org/html/2410.24164v1)
- Figure AI: [Helix](https://www.figure.ai/news/helix)
- OpenAI: [Announcing Stargate](https://openai.com/index/announcing-the-stargate-project/)
- OpenAI: [Building compute infrastructure for the Intelligence Age](https://openai.com/index/building-the-compute-infrastructure-for-the-intelligence-age/)
- Google: [Ironwood TPU](https://blog.google/innovation-and-ai/infrastructure-and-cloud/google-cloud/ironwood-tpu-age-of-inference/)
- NVIDIA: [Rubin platform](https://nvidianews.nvidia.com/news/rubin-platform-ai-supercomputer)
- NVIDIA: [Vera Rubin platform](https://nvidianews.nvidia.com/news/nvidia-vera-rubin-platform)
- UK AISI: [Evaluation of GPT-5.5 cyber capabilities](https://www.aisi.gov.uk/blog/our-evaluation-of-openais-gpt-5-5-cyber-capabilities)
