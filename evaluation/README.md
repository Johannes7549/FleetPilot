# Evaluation

Run everything with one command from the repository root:

```bash
python scripts/run_evaluations.py
```

LLM evaluation suite (separate; mocked provider by default, `--live` for a real model):

```bash
python scripts/run_llm_evaluations.py
python scripts/run_llm_evaluations.py --live
```

* Deterministic cases: [`backend/app/evaluation/cases.py`](../backend/app/evaluation/cases.py)
* LLM cases: [`backend/app/evaluation/llm_cases.py`](../backend/app/evaluation/llm_cases.py), mock transport
  [`llm_mock.py`](../backend/app/evaluation/llm_mock.py), runner [`llm_runner.py`](../backend/app/evaluation/llm_runner.py)
* LLM report: [`results/llm_latest.md`](results/llm_latest.md)
* Runner: [`backend/app/evaluation/runner.py`](../backend/app/evaluation/runner.py)
* Reports: [`results/latest.md`](results/latest.md), `results/latest.json`
* Methodology: [`docs/evaluation.md`](../docs/evaluation.md)
