"""Level 0: a chat program. It can talk about your code, but it cannot see or touch it."""
import sys
from model import Meter, ask


def run_task(prompt, workdir="."):
    """Give the model one task and return what happened. Every later level keeps this signature."""
    meter = Meter()
    messages = [{"role": "user", "content": prompt}]
    response = ask(messages, meter)
    text = "".join(b.text for b in response.content if b.type == "text")
    return {"final_text": text, "steps": 0, "stopped": response.stop_reason, "meter": meter}


if __name__ == "__main__":
    # the "API is stateless" demo: we keep the history ourselves and resend all of it each turn
    meter, messages = Meter(), []
    while True:
        messages.append({"role": "user", "content": input("you> ")})
        response = ask(messages, meter)
        messages.append({"role": "assistant", "content": response.content})
        print("".join(b.text for b in response.content if b.type == "text"))
        print(f"  [{len(messages)} messages resent, ${meter.cost:.4f} so far]", file=sys.stderr)
