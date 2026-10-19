# Premium CFO değerlendirme sözleşmesi (PR-17)

Bu değerlendirme katmanı yalnızca sentetik metinler üzerinde çalışır. Excel,
SQLite, OneDrive, üretim servisi veya gerçek LLM çağrısı yapmaz.

## Corpus ve sızıntı kontrolü

`tests/fixtures/cfo_eval_manifest.v1.json` corpus sürümünü tanımlar.
`scripts/cfo_evaluation.py` aynı girdide aynı corpus'u üretir ve 3.000 vakayı
şablon aileleriyle `train`, `validation` ve `holdout` bölümlerine ayırır.
Bir template ailesi iki bölüme dağılırsa veya normalize edilmiş metin tekrar
ederse değerlendirme başarısız olur. Holdout cevapları eğitim girdisi değildir.

## Ölçülen metrikler

- intent accuracy
- tutar extraction accuracy
- para birimi accuracy
- sorgunun yazma taslağına dönüşmesi (`read_write_safety_violations`)
- split sızıntısı (`leakage_violations`)

PR-17 deterministik katmanı ölçer. Gerçek Ollama/Qwen ölçümü yapılmadığı için
`live_model_evaluation=NOT_RUN`, üretim doğrulaması ise `NOT_VERIFIED` olarak
kalır. Bu değerler 99% model olgunluğu iddiası değildir.

Çalıştırma:

```powershell
py -3.12 -m pytest -q tests/test_premium_cfo_evaluation.py
```
