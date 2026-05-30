import sys
import time
import threading

from engine.board import Board
from engine.move_generator import MoveGenerator


STARTPOS_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

_OPTIONS = {
    "Hash":          ("spin", 128,  1,    4096),  # (type, default, min, max) in MB
    "Move Overhead": ("spin", 50,   0,    5000),
    "Threads":       ("spin",  1,   1,       8),
    "MultiPV":       ("spin",  1,   1,     256),
    "Skill Level":   ("spin", 20,   0,      20),
    "Ponder":        ("check", 0,   0,       1),
}


def square_to_coord(square):
    row, col = square
    return f"{chr(ord('a') + col)}{8 - row}"


def coord_to_square(text):
    return 8 - int(text[1]), ord(text[0]) - ord("a")


def move_to_uci(move):
    if move is None:
        return "0000"
    start, end = move[0], move[1]
    promo = move[2] if len(move) > 2 else None
    result = square_to_coord(start) + square_to_coord(end)
    if promo is not None:
        result += promo.lower()
    return result


def apply_uci_move(board, mg, move_text):
    start = coord_to_square(move_text[:2])
    end = coord_to_square(move_text[2:4])
    promotion = move_text[4].upper() if len(move_text) > 4 else None

    if mg is not None:
        is_white = board.turn == "white"
        legal_moves = mg.generate_all_legal_moves(is_white)
        legal_endpoints = {(s, e) for s, e, _ in legal_moves}
        if (start, end) not in legal_endpoints:
            print(f"info string illegal move ignored: {move_text}", flush=True)
            return

    board.make_move(start, end, promotion)
    board.turn = "black" if board.turn == "white" else "white"
    board.record_current_position()


def configure_position(board, mg, args):
    if not args:
        return

    if args[0] == "startpos":
        board.set_fen(STARTPOS_FEN)
        move_index = 1
    elif args[0] == "fen":
        if "moves" in args:
            move_index = args.index("moves")
            fen = " ".join(args[1:move_index])
        else:
            move_index = len(args)
            fen = " ".join(args[1:])
        board.set_fen(fen)
    else:
        return

    mg.in_opening = True
    if move_index < len(args) and args[move_index] == "moves":
        for move_text in args[move_index + 1:]:
            apply_uci_move(board, mg, move_text)
        mg.in_opening = False


def _compute_movetime(args, is_white):
    """Return search time in seconds from go parameters, or None if not applicable."""
    def get_int(key, default=None):
        if key in args:
            idx = args.index(key)
            if idx + 1 < len(args):
                try:
                    return int(args[idx + 1])
                except ValueError:
                    pass
        return default

    wtime = get_int("wtime")
    btime = get_int("btime")
    winc  = get_int("winc",  0)
    binc  = get_int("binc",  0)
    movestogo = get_int("movestogo")
    overhead_ms = get_int("Move Overhead", _OPTIONS["Move Overhead"][1])

    time_left_ms = (wtime if is_white else btime)
    inc_ms       = (winc  if is_white else binc)

    if time_left_ms is None:
        return None

    if movestogo and movestogo > 0:
        allotted_ms = time_left_ms / movestogo + inc_ms
    else:
        allotted_ms = time_left_ms / 40 + inc_ms * 0.8

    # Never use more than half the remaining clock
    allotted_ms = min(allotted_ms, time_left_ms / 2)
    allotted_ms -= overhead_ms
    return max(0.05, allotted_ms / 1000.0)


def parse_go(args, is_white):
    depth = 64
    movetime = None

    if "depth" in args:
        idx = args.index("depth")
        if idx + 1 < len(args):
            depth = int(args[idx + 1])

    if "movetime" in args:
        idx = args.index("movetime")
        if idx + 1 < len(args):
            movetime = max(0.01, int(args[idx + 1]) / 1000.0)

    if movetime is None:
        movetime = _compute_movetime(args, is_white)

    return depth, movetime


def _format_score(score_pawns):
    """Return UCI score string: 'cp X' or 'mate N'."""
    mate_threshold = MoveGenerator.MATE_SCORE - 50
    if abs(score_pawns) >= mate_threshold:
        plies = MoveGenerator.MATE_SCORE - int(abs(score_pawns))
        n = max(1, (plies + 1) // 2)
        return f"mate {n if score_pawns > 0 else -n}"
    return f"cp {int(score_pawns * 100)}"


def make_info_callback(mg, t0, multipv=1):
    def callback(depth, score, pv, root_scores=None):
        elapsed_ms = max(1, int((time.perf_counter() - t0) * 1000))
        nps = int(mg.node_count / (elapsed_ms / 1000.0))
        if multipv <= 1 or not root_scores:
            pv_str = " ".join(move_to_uci(m) for m in pv)
            score_str = _format_score(score)
            line = (f"info depth {depth} score {score_str}"
                    f" nodes {mg.node_count} nps {nps} time {elapsed_ms}")
            if pv_str:
                line += f" pv {pv_str}"
            print(line, flush=True)
            return

        limit = min(int(multipv), len(root_scores))
        for i in range(limit):
            move, move_score = root_scores[i]
            score_str = _format_score(move_score)
            line = (f"info depth {depth} multipv {i + 1} score {score_str}"
                    f" nodes {mg.node_count} nps {nps} time {elapsed_ms}")
            if i == 0:
                pv_str = " ".join(move_to_uci(m) for m in pv)
            else:
                pv_str = move_to_uci(move)
            if pv_str:
                line += f" pv {pv_str}"
            print(line, flush=True)
    return callback


def main():
    board = Board()
    mg = MoveGenerator(board)
    multipv = _OPTIONS["MultiPV"][1]
    threads = _OPTIONS["Threads"][1]
    skill_level = _OPTIONS["Skill Level"][1]
    ponder_enabled = bool(_OPTIONS["Ponder"][1])
    # Surface some engine prefs onto the move generator for tooling/UI.
    mg.threads = threads
    mg.skill_level = skill_level
    mg.ponder = ponder_enabled

    # Pondering/background search state
    ponder_state = {
        "thread": None,
        "lock": threading.Lock(),
        "result": None,
        "bg_mg": None,
        "committed": False,
        "bestmove_sent": False,
        "pending_movetime": None,
    }

    def _bg_search(mg_obj, board_snapshot, depth, movetime, multipv, use_book, is_white):
        # Run find_best_move in background and stash the result.
        try:
            best_move, score = mg_obj.find_best_move(
                depth, is_white, max_time=movetime, verbose=False,
                on_depth_complete=make_info_callback(mg_obj, time.perf_counter(), multipv=multipv),
                use_book=use_book,
            )
        except Exception:
            best_move, score = (None, 0)
        with ponder_state["lock"]:
            ponder_state["result"] = (best_move, score)
            ponder_state["bg_mg"] = mg_obj
            ponder_state["thread"] = None

        if best_move is None:
            return

        # If ponderhit already happened, emit the move immediately. Otherwise
        # wait until the commit signal arrives and then emit without restarting.
        while True:
            with ponder_state["lock"]:
                committed = ponder_state["committed"]
                already_sent = ponder_state["bestmove_sent"]
                current_result = ponder_state["result"]
                current_bg_mg = ponder_state["bg_mg"]
                if committed and (not already_sent) and current_result is not None:
                    if current_bg_mg is not None:
                        mg.import_transposition_table(current_bg_mg)
                    print(f"bestmove {move_to_uci(best_move)}", flush=True)
                    ponder_state["bestmove_sent"] = True
                    ponder_state["result"] = None
                    break
            time.sleep(0.01)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split()
        command = parts[0]
        args = parts[1:]

        if command == "uci":
            print("id name Rohans Engine")
            print("id author Ujan Dey")
            for name, (opt_type, default, mn, mx) in _OPTIONS.items():
                print(f"option name {name} type {opt_type} default {default} min {mn} max {mx}")
            print("uciok")

        elif command == "setoption":
            # setoption name <Name> value <val>
            if "name" in args:
                name_idx = args.index("name")
                if "value" in args:
                    val_idx = args.index("value")
                    opt_name = " ".join(args[name_idx + 1:val_idx])
                    val_str  = " ".join(args[val_idx + 1:])
                else:
                    opt_name = " ".join(args[name_idx + 1:])
                    val_str  = None

                if opt_name == "Hash" and val_str is not None:
                    try:
                        mg.set_hash_size(int(val_str))
                    except ValueError:
                        pass
                elif opt_name == "MultiPV" and val_str is not None:
                    try:
                        multipv = max(_OPTIONS["MultiPV"][2], min(_OPTIONS["MultiPV"][3], int(val_str)))
                    except ValueError:
                        pass
                elif opt_name == "Move Overhead" and val_str is not None:
                    try:
                        _OPTIONS["Move Overhead"] = (_OPTIONS["Move Overhead"][0],
                                                     int(val_str),
                                                     *_OPTIONS["Move Overhead"][2:])
                    except ValueError:
                        pass
                elif opt_name == "Threads" and val_str is not None:
                    try:
                        v = max(_OPTIONS["Threads"][2], min(_OPTIONS["Threads"][3], int(val_str)))
                        _OPTIONS["Threads"] = (_OPTIONS["Threads"][0], v, *_OPTIONS["Threads"][2:])
                        threads = v
                        mg.threads = v
                    except ValueError:
                        pass
                elif opt_name == "Skill Level" and val_str is not None:
                    try:
                        v = max(_OPTIONS["Skill Level"][2], min(_OPTIONS["Skill Level"][3], int(val_str)))
                        _OPTIONS["Skill Level"] = (_OPTIONS["Skill Level"][0], v, *_OPTIONS["Skill Level"][2:])
                        skill_level = v
                        mg.skill_level = v
                    except ValueError:
                        pass
                elif opt_name == "Ponder":
                    # value may be omitted for check types; treat presence as True/False
                    if val_str is None:
                        ponder_enabled = True
                        _OPTIONS["Ponder"] = (_OPTIONS["Ponder"][0], 1, *_OPTIONS["Ponder"][2:])
                        mg.ponder = True
                    else:
                        try:
                            v = 1 if val_str.lower() in ("1", "true", "on") else 0
                            _OPTIONS["Ponder"] = (_OPTIONS["Ponder"][0], v, *_OPTIONS["Ponder"][2:])
                            ponder_enabled = bool(v)
                            mg.ponder = ponder_enabled
                        except Exception:
                            pass

        elif command == "isready":
            print("readyok")

        elif command == "ucinewgame":
            board = Board()
            mg = MoveGenerator(board)

        elif command == "position":
            configure_position(board, mg, args)

        elif command == "go":
            is_white = board.turn == "white"
            depth, movetime = parse_go(args, is_white)
            t0 = time.perf_counter()
            callback = make_info_callback(mg, t0, multipv=multipv)
            use_book = (multipv <= 1)

            if "ponder" in args:
                # Start background pondering search and return immediately.
                # Clone current board into a background MoveGenerator so the
                # ponder search can build a separate transposition table.
                bg_board = Board()
                try:
                    bg_board.set_fen(board.to_fen(board.halfmove_clock))
                except Exception:
                    # Fallback to copying via FEN start position if something odd.
                    bg_board.set_fen(board.to_fen(board.halfmove_clock))
                bg_mg = MoveGenerator(bg_board)
                # Inherit tt size and options from main mg
                bg_mg.tt_max_entries = mg.tt_max_entries
                bg_mg.killers = [k[:] for k in mg.killers]
                bg_mg.history = mg.history.copy()
                bg_mg.in_opening = mg.in_opening
                with ponder_state["lock"]:
                    # Clear any previous result
                    ponder_state["result"] = None
                    ponder_state["bg_mg"] = bg_mg
                    ponder_state["committed"] = False
                    ponder_state["bestmove_sent"] = False
                    ponder_state["pending_movetime"] = movetime
                    bg_mg.stop_search = False
                    th = threading.Thread(
                        target=_bg_search,
                        args=(bg_mg, None, depth, None, multipv, use_book, is_white),
                        daemon=True,
                    )
                    ponder_state["thread"] = th
                    th.start()
                # Do not emit bestmove now; wait for ponderhit or stop.
                continue

            # Normal (blocking) search
            best_move, score = mg.find_best_move(
                depth, is_white, max_time=movetime, verbose=False,
                on_depth_complete=callback,
                use_book=use_book,
            )
            # Opening book or sub-depth-1 timeout: emit a minimal info line
            if mg.last_completed_depth == 0 and best_move is not None:
                elapsed_ms = max(1, int((time.perf_counter() - t0) * 1000))
                pv_str = move_to_uci(best_move)
                print(f"info depth 1 score {_format_score(score)}"
                      f" nodes {mg.node_count} nps 0 time {elapsed_ms} pv {pv_str}", flush=True)
            print(f"bestmove {move_to_uci(best_move)}", flush=True)

        elif command == "stop":
            # Stop any current foreground search
            mg.stop_search = True
            # If we have a background ponder search, stop it and let the
            # background thread emit bestmove once it has a result.
            with ponder_state["lock"]:
                th = ponder_state.get("thread")
                pmg = ponder_state.get("bg_mg")
                ponder_state["committed"] = True
            if th is not None and th.is_alive():
                if pmg is not None:
                    pmg.stop_search = True
                th.join(timeout=1.0)
            with ponder_state["lock"]:
                ponder_state["thread"] = None

        elif command == "quit":
            break

        elif command == "ponderhit":
            # Opponent played the pondered move: let the background search continue
            # and give it a real time budget from this point on.
            with ponder_state["lock"]:
                th = ponder_state.get("thread")
                pmg = ponder_state.get("bg_mg")
                pending_movetime = ponder_state.get("pending_movetime")
                ponder_state["committed"] = True
            if pmg is not None and pending_movetime is not None:
                pmg.search_deadline = time.perf_counter() + pending_movetime

        sys.stdout.flush()


if __name__ == "__main__":
    main()
