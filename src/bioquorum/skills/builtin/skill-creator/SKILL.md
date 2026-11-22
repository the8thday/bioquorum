---
name: skill-creator
description: Draft or revise a reusable BioQuorum project Skill when the researcher asks to capture a recurring workflow.
license: Apache-2.0
---
Start from one task the researcher expects to repeat. Identify the input, the decision the instructions should guide and the observable result. Include steps only where the project has a specific convention, tool or evidence requirement.

Return a complete SKILL.md in one Markdown code block. Begin with YAML frontmatter containing a lowercase, hyphenated name and a description that says when to use the skill. Follow it with the instructions. Mention only tools the target project actually provides. Describe any additional connector as a prerequisite. Keep credentials and individual research records out of reusable instructions.

Check the draft against the example task and one nearby task that should not trigger it. To activate it, the researcher saves the draft as SKILL.md and chooses Skills → Install skill. A draft in a research response has no effect on future sessions until installed.
