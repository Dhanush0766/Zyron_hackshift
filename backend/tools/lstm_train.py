"""Offline training of the compliance LSTM (IDF-B §7.5, Claim 4). Dev-time only (needs numpy).

    python tools/lstm_train.py            # train, evaluate, export
    python tools/lstm_train.py --gradcheck

DATA IS SYNTHETIC. The generator below builds an effect "gate failure in recent
sessions -> slower pickup next session" INTO the data. So any improvement from the
gate-failure features only shows the model can learn an effect we put there. It is
NOT evidence that the effect exists in real patients. Same caveat applies to the
IDF-B §8.5 ablation, which also used synthetic data.

Exports:
  medgate/core/lstm_weights.json                 (server inference, pure Python)
  ../firmware/lib/medgate_core/lstm_weights.h    (ESP32 inference)
  ../shared/lstm_vectors.csv                     (parity test vectors for Python and C++)
  ../shared/lstm_eval.json                       (evaluation numbers quoted in the docs)
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CAP_MIN = 30.0          # pickup later than this = missed
SESSION_HOURS = (8, 14, 20)
FEATURES = ["latency_norm", "missed", "gate_fail", "gate_rate_last5", "sin_hour_next", "cos_hour_next", "weekend_next"]
GATE_COLS = [2, 3]


# ------------------------------------------------------------------ synthetic data
def make_user(rng, days=20):
    base = rng.uniform(2, 18, 3)                 # per-session typical pickup latency, minutes
    weekend = rng.uniform(-2, 6)
    p_fail = rng.beta(1.2, 6, 3)                 # per-session gate failure probability
    fail_effect = rng.uniform(3, 10)             # minutes slower after recent gate failures (built in)
    lat, missed, gate, hours, wkend = [], [], [], [], []
    recent = []
    for d in range(days):
        we = int(d % 7 in (5, 6))
        for s in range(3):
            g = int(rng.random() < p_fail[s])
            rate = sum(recent[-5:]) / max(1, len(recent[-5:]))
            l = base[s] + we * weekend + fail_effect * rate * 2 + rng.normal(0, 2)
            l = max(0.5, l)
            m = int(l >= CAP_MIN or rng.random() < 0.03 + 0.2 * rate)
            lat.append(min(l, CAP_MIN) if not m else CAP_MIN)
            missed.append(m); gate.append(g); hours.append(SESSION_HOURS[s]); wkend.append(we)
            recent.append(g)
    return np.array(lat), np.array(missed), np.array(gate), np.array(hours), np.array(wkend)


def featurize(lat, missed, gate, hours, wkend):
    T = len(lat)
    X = np.zeros((T - 1, len(FEATURES)))
    y = np.zeros(T - 1)
    for t in range(T - 1):
        rate = gate[max(0, t - 4): t + 1].mean()
        h = hours[t + 1]
        X[t] = [lat[t] / CAP_MIN, missed[t], gate[t], rate,
                math.sin(2 * math.pi * h / 24), math.cos(2 * math.pi * h / 24), wkend[t + 1]]
        y[t] = lat[t + 1] / CAP_MIN
    return X, y


# ------------------------------------------------------------------ LSTM (numpy, manual BPTT)
def sig(x):
    return 1 / (1 + np.exp(-x))


class LSTM:
    def __init__(self, n_in, n_h, rng):
        k = 1 / math.sqrt(n_h)
        self.W = rng.uniform(-k, k, (4 * n_h, n_in + n_h))   # gate order: i, f, o, g
        self.b = np.zeros(4 * n_h); self.b[n_h:2 * n_h] = 1.0  # forget bias 1
        self.Wy = rng.uniform(-k, k, n_h)
        self.by = np.array(0.3)
        self.H = n_h

    def params(self):
        return [self.W, self.b, self.Wy, self.by]

    def forward(self, X):
        H = self.H
        h = np.zeros(H); c = np.zeros(H)
        cache, out = [], []
        for x in X:
            z = np.concatenate([x, h])
            a = self.W @ z + self.b
            i, f, o, g = sig(a[:H]), sig(a[H:2 * H]), sig(a[2 * H:3 * H]), np.tanh(a[3 * H:])
            c_new = f * c + i * g
            h_new = o * np.tanh(c_new)
            out.append(self.Wy @ h_new + self.by)
            cache.append((z, i, f, o, g, c, c_new, h_new))
            h, c = h_new, c_new
        return np.array(out), cache

    def backward(self, cache, dy):
        H = self.H
        dW = np.zeros_like(self.W); db = np.zeros_like(self.b)
        dWy = np.zeros_like(self.Wy); dby = 0.0
        dh_next = np.zeros(H); dc_next = np.zeros(H)
        for t in reversed(range(len(cache))):
            z, i, f, o, g, c_prev, c, h = cache[t]
            dWy += dy[t] * h; dby += dy[t]
            dh = dy[t] * self.Wy + dh_next
            tc = np.tanh(c)
            do = dh * tc
            dc = dh * o * (1 - tc ** 2) + dc_next
            di, df, dg = dc * g, dc * c_prev, dc * i
            da = np.concatenate([di * i * (1 - i), df * f * (1 - f), do * o * (1 - o), dg * (1 - g ** 2)])
            dW += np.outer(da, z); db += da
            dz = self.W.T @ da
            dh_next = dz[-H:]
            dc_next = dc * f
        return [dW, db, dWy, np.array(dby)]


def loss_and_grads(m, X, y):
    p, cache = m.forward(X)
    err = p - y
    return float(np.mean(err ** 2)), m.backward(cache, 2 * err / len(y))


def gradcheck():
    rng = np.random.default_rng(0)
    m = LSTM(len(FEATURES), 5, rng)
    X = rng.normal(size=(6, len(FEATURES))); y = rng.normal(size=6)
    _, grads = loss_and_grads(m, X, y)
    worst = 0.0
    for P, G in zip(m.params(), grads):
        flat, gflat = P.reshape(-1), G.reshape(-1)
        for idx in rng.choice(flat.size, min(20, flat.size), replace=False):
            old = flat[idx]
            flat[idx] = old + 1e-5; lp, _ = loss_and_grads(m, X, y)
            flat[idx] = old - 1e-5; lm, _ = loss_and_grads(m, X, y)
            flat[idx] = old
            num = (lp - lm) / 2e-5
            worst = max(worst, abs(num - gflat[idx]) / max(1e-8, abs(num) + abs(gflat[idx])))
    print(f"gradient check: max relative error {worst:.2e}")
    return worst


def train(seqs, n_h, mask_gate, epochs, seed):
    rng = np.random.default_rng(seed)
    m = LSTM(len(FEATURES), n_h, rng)
    mom = [np.zeros_like(p) for p in m.params()]; vel = [np.zeros_like(p) for p in m.params()]
    step, lr = 0, 0.01
    for ep in range(epochs):
        for j in rng.permutation(len(seqs)):
            X, y = seqs[j]
            if mask_gate:
                X = X.copy(); X[:, GATE_COLS] = 0
            _, grads = loss_and_grads(m, X, y)
            step += 1
            for p, g, a, v in zip(m.params(), grads, mom, vel):
                g = np.clip(g, -1, 1)
                a *= 0.9; a += 0.1 * g
                v *= 0.999; v += 0.001 * g * g
                p -= lr * (a / (1 - 0.9 ** step)) / (np.sqrt(v / (1 - 0.999 ** step)) + 1e-8)
    return m


def mae_minutes(pred, y):
    return float(np.mean(np.abs(pred - y)) * CAP_MIN)


def evaluate(m, seqs, mask_gate=False, warmup=5):
    errs = []
    for X, y in seqs:
        if mask_gate:
            X = X.copy(); X[:, GATE_COLS] = 0
        p, _ = m.forward(X)
        errs.append(np.abs(np.clip(p, 0, 1) - y)[warmup:])
    return float(np.mean(np.concatenate(errs)) * CAP_MIN)


def baselines(train_seqs, test_seqs, warmup=5):
    glob = np.mean(np.concatenate([y for _, y in train_seqs]))
    static = float(np.mean(np.concatenate([np.abs(glob - y)[warmup:] for _, y in test_seqs])) * CAP_MIN)
    errs = []
    for X, y in test_seqs:
        lat = np.concatenate([[X[0, 0]], y])          # latencies of sessions 0..T
        for t in range(warmup, len(y)):
            same = lat[t + 1 - 3::-3][:3]             # same session, previous 3 days
            errs.append(abs(np.mean(same) - y[t]))
    return static, float(np.mean(errs) * CAP_MIN)


def export(m, path_json, path_h, vectors_path, test_seqs):
    w = {"features": FEATURES, "hidden": m.H, "cap_minutes": CAP_MIN, "gate_order": "i,f,o,g",
         "W": m.W.round(6).tolist(), "b": m.b.round(6).tolist(), "Wy": m.Wy.round(6).tolist(),
         "by": round(float(m.by), 6), "data": "SYNTHETIC - see tools/lstm_train.py"}
    Path(path_json).write_text(json.dumps(w))
    n_in = len(FEATURES)
    fl = lambda a: ", ".join(f"{v:.6f}f" for v in np.asarray(a).reshape(-1).round(6))
    Path(path_h).write_text(
        "// Generated by backend/tools/lstm_train.py - do not edit. Trained on SYNTHETIC data.\n#pragma once\n"
        f"#define MG_LSTM_IN {n_in}\n#define MG_LSTM_H {m.H}\n#define MG_LSTM_CAP_MIN {CAP_MIN:.1f}f\n"
        f"static const float MG_LSTM_W[{4 * m.H * (n_in + m.H)}] = {{{fl(m.W)}}};\n"
        f"static const float MG_LSTM_B[{4 * m.H}] = {{{fl(m.b)}}};\n"
        f"static const float MG_LSTM_WY[{m.H}] = {{{fl(m.Wy)}}};\n"
        f"static const float MG_LSTM_BY = {float(m.by):.6f}f;\n")
    # parity vectors: 5 test sequences of 10 steps -> expected final prediction (minutes)
    rows = ["seq,step," + ",".join(FEATURES) + ",expected_minutes"]
    m2 = LSTM(n_in, m.H, np.random.default_rng(0))
    m2.W, m2.b, m2.Wy, m2.by = np.array(w["W"]), np.array(w["b"]), np.array(w["Wy"]), np.array(w["by"])
    for s, (X, _) in enumerate(test_seqs[:5]):
        Xs = X[:10]
        p, _ = m2.forward(Xs)
        for t, x in enumerate(Xs):
            exp = f"{float(np.clip(p[t], 0, 1)) * CAP_MIN:.5f}" if t == len(Xs) - 1 else ""
            rows.append(f"{s},{t}," + ",".join(f"{v:.6f}" for v in x) + "," + exp)
    Path(vectors_path).write_text("\n".join(rows) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gradcheck", action="store_true")
    ap.add_argument("--users", type=int, default=160)
    ap.add_argument("--epochs", type=int, default=40)
    a = ap.parse_args()
    if a.gradcheck:
        raise SystemExit(0 if gradcheck() < 1e-5 else 1)
    rng = np.random.default_rng(42)
    seqs = [featurize(*make_user(rng)) for _ in range(a.users + 40)]
    train_s, test_s = seqs[:a.users], seqs[a.users:]           # split BY USER: no leakage
    full = train(train_s, 12, False, a.epochs, 1)
    abl = train(train_s, 12, True, a.epochs, 1)
    static, rolling = baselines(train_s, test_s)
    res = {"data": "SYNTHETIC (generator in tools/lstm_train.py builds in a gate-failure effect)",
           "train_users": len(train_s), "test_users": len(test_s), "sessions_per_user": 60,
           "metric": "MAE of next-session pickup latency, minutes, held-out users, after 5 warm-up sessions",
           "static_mean_baseline": round(static, 2), "rolling_same_session_baseline": round(rolling, 2),
           "lstm_without_gate_features": round(evaluate(abl, test_s, True), 2),
           "lstm_with_gate_features": round(evaluate(full, test_s), 2),
           "params": int(sum(p.size for p in full.params())), "weights_bytes_float32": int(sum(p.size for p in full.params()) * 4)}
    print(json.dumps(res, indent=2))
    export(full, ROOT / "medgate/core/lstm_weights.json", ROOT.parent / "firmware/lib/medgate_core/lstm_weights.h",
           ROOT.parent / "shared/lstm_vectors.csv", test_s)
    (ROOT.parent / "shared/lstm_eval.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
