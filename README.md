# <img src="./assets/logo.png" alt="RoboClaw logo" width="28"> RoboClaw: An Agentic Framework for Scalable Long-Horizon Robotic Tasks

<p align="center">
  <a href="https://roboclaw-agibot.github.io/">
    <img src="https://img.shields.io/badge/Project-Website-green" alt="Project Website">
  </a>
  <a href="https://arxiv.org/abs/2603.11558">
    <img src="https://img.shields.io/badge/arXiv-2603.11558-b31b1b" alt="arXiv">
  </a>
  <img src="https://img.shields.io/badge/Status-Ready-green" alt="Status: Paper assets only">
  <img src="https://img.shields.io/badge/Open--Source-Released-blue" alt="Open-source release plan">
</p>

<p align="center"><strong>Collaborating Institutions</strong></p>
<p align="center">
  <img src="./assets/Agi-logo.png" alt="AgiBot" height="104">
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <img src="./assets/ScaleLab-logo.png" alt="ScaleLab" height="104">
</p>

**[Website](https://roboclaw-agibot.github.io/) | [arXiv](https://arxiv.org/abs/2603.11558)**

RoboClaw is an agentic robotics framework for long-horizon manipulation. It uses a vision-language model as the high-level controller and keeps the same agent in the loop during data collection, policy learning, and deployment.

Instead of treating those stages as separate systems, RoboClaw lets the agent reason over context, choose skills, monitor execution, and feed deployment experience back into training.

![RoboClaw teaser](assets/teaser.jpg)

> 🎉 The public codebase has been released!

## ✨ Highlights
- One agent loop across collection, training, and deployment
- Vision-language reasoning for subtask selection and execution monitoring
- **Entangled Action Pairs (EAP)** for self-resetting data collection
- Failure recovery through retrying, replanning, or human escalation when needed
- Real-world evaluation on long-horizon manipulation tasks with the Agibot G01 platform

## 📊 Main Results
- On long-horizon tasks, RoboClaw improves success rate by **25%** over baseline pipelines
- Across the full robot lifecycle, it reduces human time investment by **53.7%**
- For the same amount of collected data, a manual pipeline requires about **2.16x** more human time
- During rollout, the manual baseline needs about **8.04x** more human intervention
- Forward policy success rates improve steadily across all four tested subtasks as rollout iterations increase

## 🧠 What RoboClaw Does
RoboClaw keeps the robot inside a single closed loop:

- During data collection, the agent observes the scene, selects the current subtask, and triggers policy execution through MCP-style tools
- For each manipulation skill, RoboClaw pairs a forward behavior with an inverse reset behavior through **Entangled Action Pairs (EAP)**, enabling self-resetting collection loops
- During deployment, the same agent monitors progress, switches skills when needed, retries or replans after failures, and escalates to humans only when recovery is unreliable or safety constraints are reached
- Execution trajectories are fed back into training, so deployment also becomes a source of new experience

## 🧪 Experimental Scenarios
We evaluate RoboClaw in several real-world settings:

- bedroom vanity table organization
- kitchen shelf organization
- study desk organization
- convenience-store shelf retrieval

The single-skill evaluations cover four representative manipulation tasks:

- body lotion placement
- primer placement with drawer closing
- lipstick insertion
- tissue wipe

## 🚀 Quick Start Guide

First, clone the repository:

```bash
git clone https://github.com/RoboClaw-Robotics/RoboClaw.git ~/RoboClaw
cd ~/RoboClaw
```

Then, prepare the `.env` file in the project root:

```bash
cd ~/RoboClaw
cp .env.example .env
```

Then fill in the tokens and credentials in `.env`:

```dotenv
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_VERIFICATION_TOKEN=xxx
FEISHU_EVENT_RECEIVER=long_connection
OPENAI_API_KEY=xxx
```

Configuration details:

- `FEISHU_APP_ID`: Feishu application App ID (optional)
- `FEISHU_APP_SECRET`: Feishu application App Secret (optional)
- `FEISHU_VERIFICATION_TOKEN`: Event verification token (optional)
- `FEISHU_EVENT_RECEIVER=long_connection`: Enables long-connection mode (optional)
- `OPENAI_API_KEY`: OpenAI API key (required)

After updating `.env`, initialize the project:

```bash
cd ~/RoboClaw
make init
```

### 🔧 Common Commands

- Start the TUI: `make run_tui`
- Start the GUI: `make run_gui`
- Tag0-to-Tag1 pick-and-place script notes: [`scripts/README_PICK_PLACE_TAGS.md`](scripts/README_PICK_PLACE_TAGS.md)

## 📄 Paper
- Title: *RoboClaw: An Agentic Framework for Scalable Long-Horizon Robotic Tasks*
- Website: [https://roboclaw-agibot.github.io/](https://roboclaw-agibot.github.io/)
- PDF: [assets/paper.pdf](assets/paper.pdf)
- arXiv: [arXiv](https://arxiv.org/abs/2603.11558)

## 🚧 Open-Source Release
The current open-source release is primarily validated on the **Agibot G01** platform.

For complete usage (including VLA model deployment), the corresponding `.whl` package is available upon request via private message.

## 📚 Citation
If RoboClaw is useful for your research, please cite: 

```bibtex
@misc{li2026roboclaw,
  title={RoboClaw: An Agentic Framework for Scalable Long-Horizon Robotic Tasks},
  author={Ruiying Li and Yunlang Zhou and YuYao Zhu and Kylin Chen and Jingyuan Wang and Sukai Wang and Kongtao Hu and Minhui Yu and Bowen Jiang and Zhan Su and Jiayao Ma and Xin He and Yongjian Shen and Yangyang and Guanghui Ren and Maoqing Yao and Wenhao Wang and Yao Mu},
  year={2026},
  eprint={2603.11558},
  archivePrefix={arXiv},
  primaryClass={cs.RO},
  url={https://arxiv.org/abs/2603.11558}, 
}
```
