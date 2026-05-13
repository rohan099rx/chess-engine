import unittest

from engine.board import Board
from engine.move_generator import MoveGenerator


class MultiPvRootScoresTests(unittest.TestCase):
    def test_root_scores_include_all_legal_moves_depth1(self):
        board = Board()
        mg = MoveGenerator(board)

        captured = {"root_scores": None}

        def on_depth_complete(depth, score, pv, root_scores=None):
            if root_scores is not None:
                captured["root_scores"] = root_scores

        is_white = board.turn == "white"
        mg.find_best_move(
            depth=1,
            is_white_turn=is_white,
            verbose=False,
            on_depth_complete=on_depth_complete,
            use_book=False,
        )

        root_scores = captured["root_scores"]
        self.assertIsNotNone(root_scores)

        legal_moves = mg.generate_all_legal_moves(is_white)
        self.assertEqual(len(root_scores), len(legal_moves))

        moves_only = [m for (m, _) in root_scores]
        self.assertEqual(len(set(moves_only)), len(moves_only))

        scores_only = [s for (_, s) in root_scores]
        self.assertTrue(all(scores_only[i] >= scores_only[i + 1] for i in range(len(scores_only) - 1)))


if __name__ == "__main__":
    unittest.main()
