# ml_package — where the real model plugs in

1. Put your Python package here, e.g. `ml_package/dishari_ml/tagger.py` with a class `Tagger`
   (start from `example_tagger/tagger.py`).
2. Put weights/tokenizer/label maps in `ml_models/current/` (this folder is mounted at `/app/ml_models`).
3. Add extra pip packages to `requirements-ml.txt`, then `docker compose build api`.
4. Check the contract offline: `python scripts/check_ml_contract.py dishari_ml.tagger:Tagger --path ml_models/current`
5. Switch the server: in `.env` set `TAGGER_IMPL=dishari_ml.tagger:Tagger`, restart
   (or hot-swap: `POST /v1/admin/models` as admin).
6. Tag questions saved while the model was down: `POST /v1/admin/retag-pending`.

Full contract: `docs/ML_CONTRACT.md`. IDs must match `seeds/taxonomy.json` exactly.
