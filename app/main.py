import argparse
import json
from app.config import load_settings
from app.assistant import AnalyticsAssistant

def main():
    parser = argparse.ArgumentParser(description="Grounded BigQuery analytics assistant PoC")
    parser.add_argument("question", nargs="*", help="Natural-language analytics question")
    args = parser.parse_args()
    question = " ".join(args.question).strip()
    if not question:
        question = input("Ask a BigQuery question: ").strip()
    assistant = AnalyticsAssistant(load_settings())
    try:
        result = assistant.ask(question)
    except Exception as exc:
        # Never convert a failure into a fabricated result.
        result = {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}
    print(json.dumps(result, indent=2, default=str))

if __name__ == "__main__":
    main()
