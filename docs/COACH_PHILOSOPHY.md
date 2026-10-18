# Coach chat philosophy and sources

The local Claude chat uses [the editable system prompt](../prompts/coach-chat.md). It is inspired by Andreas Almgren's publicly described *principles*, adapted to this athlete's saved goal, running days, recent runs, wellness and feedback. It does not copy an elite training plan.

In a [2022 interview with Marathon.se](https://www.marathon.se/lopningen/tavling/andreas-almgren-om-traningen-och-succeloppet-i-bryssel), Almgren describes long-term consistency, keeping margin, controlling effort with lactate rather than chasing a split, stopping a session when the measured load rose too far, and avoiding sessions that are harder than needed. The coach translates those points into controlled, repeatable effort for this athlete. The interview's exact interval example and paces are not automatically prescribed.

The [Swedish Athletics Federation interview](https://www.friidrott.se/tavling-landslag/nyhetsarkiv-tavling-landslag/almgren-taggad-pa-terrangdebut-gillar-att-testa-nya-grejer/) provides further context about Almgren's 5 km focus and training changes. These sources describe his choices; they do not establish that his volume, double-threshold sessions, or lactate values are suitable for another runner.

Athlete-specific conclusions must be grounded in the saved context sent with each question: the confirmed goal, running days, recent imported runs, wellness, feedback and reviewed memory. The prompt deliberately reads that live context rather than freezing facts into its instructions. An imported window is not a lifetime history or a current race benchmark.

Prefer a different coaching philosophy? Edit [prompts/coach-chat.md](../prompts/coach-chat.md) and [prompts/coach-plan-api.md](../prompts/coach-plan-api.md). New chat answers use prompt edits without restarting the dashboard.
