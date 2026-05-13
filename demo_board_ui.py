#!/usr/bin/env python3
"""
Chess Board UI Demo - Shows all improved board display and analysis features
"""

from engine.board import Board

def demo_board_display():
    """Demonstrate the improved board display with algebraic coordinates."""
    print("\n" + "="*70)
    print("DEMO: IMPROVED BOARD DISPLAY WITH ALGEBRAIC COORDINATES")
    print("="*70)
    
    board = Board()
    
    # Show simple text board
    print("\n1. Simple Text Board (with a-h, 1-8 coordinates):")
    board.print_board_simple()
    
    # Show highlighted moves
    print("\n2. Example Board with Highlighted Squares:")
    highlight = {(6, 4), (4, 4)}  # e2 and e4
    board.print_board_simple(highlight_squares=highlight)

def demo_algebraic_notation():
    """Demonstrate coordinate conversion."""
    print("\n" + "="*70)
    print("DEMO: ALGEBRAIC NOTATION CONVERSION")
    print("="*70)
    
    board = Board()
    
    test_squares = [
        (0, 0, "a8"), (0, 7, "h8"),
        (7, 0, "a1"), (7, 7, "h1"),
        (6, 4, "e2"), (4, 4, "e4"),
    ]
    
    print("\nCoord Conversion (board coords ↔ algebraic):")
    for row, col, expected_alg in test_squares:
        alg = board.square_to_algebraic(row, col)
        back = board.algebraic_to_square(alg)
        status = "✓" if alg == expected_alg and back == (row, col) else "✗"
        print(f"  {status} ({row}, {col}) → {alg} → {back}")

def demo_position_evaluation():
    """Demonstrate position evaluation."""
    print("\n" + "="*70)
    print("DEMO: POSITION EVALUATION & ANALYSIS")
    print("="*70)
    
    board = Board()
    
    # Starting position
    print("\n1. STARTING POSITION:")
    board.print_position_analysis()
    
    # After 1.e4
    print("\n2. AFTER 1.e4:")
    board.make_move((6, 4), (4, 4))
    board.turn = "black"
    board.print_position_analysis()
    
    # After 1...e5
    print("\n3. AFTER 1...e5:")
    board.make_move((1, 4), (3, 4))
    board.turn = "white"
    board.print_position_analysis()
    
    # After 2.Nf3
    print("\n4. AFTER 2.Nf3:")
    board.make_move((7, 6), (5, 5))
    board.turn = "black"
    board.print_position_analysis()

def demo_move_suggestions():
    """Demonstrate move suggestions with evaluation."""
    print("\n" + "="*70)
    print("DEMO: MOVE SUGGESTIONS WITH EVALUATION")
    print("="*70)
    
    board = Board()
    
    # Generate some sample moves (simplified - real implementation uses MoveGenerator)
    sample_moves = [
        ((6, 4), (4, 4), ""),  # e2-e4
        ((6, 5), (4, 5), ""),  # f2-f4
        ((6, 3), (4, 3), ""),  # d2-d4
        ((6, 0), (5, 0), ""),  # a2-a3
        ((7, 6), (5, 5), ""),  # g1-f3
    ]
    
    print("\n1. STARTING POSITION - WHITE TO MOVE:")
    print(f"\nCurrent evaluation: {board.evaluate_position():.2f}\n")
    
    suggestions = board.get_best_move_suggestions(sample_moves, max_suggestions=5)
    print("Best Move Suggestions:")
    for suggestion in suggestions:
        print(suggestion)
    
    # After 1.e4
    print("\n" + "-"*70)
    print("\n2. AFTER 1.e4 - BLACK TO MOVE:")
    board.make_move((6, 4), (4, 4))
    board.turn = "black"
    
    sample_moves_black = [
        ((1, 4), (3, 4), ""),  # e7-e5
        ((1, 3), (3, 3), ""),  # d7-d5
        ((1, 6), (2, 6), ""),  # g7-g6
        ((0, 6), (2, 5), ""),  # g8-f6
    ]
    
    print(f"\nCurrent evaluation: {board.evaluate_position():.2f}\n")
    
    suggestions = board.get_best_move_suggestions(sample_moves_black, max_suggestions=4)
    print("Best Move Suggestions:")
    for suggestion in suggestions:
        print(suggestion)

def demo_material_tracking():
    """Demonstrate material tracking and capture detection."""
    print("\n" + "="*70)
    print("DEMO: MATERIAL TRACKING & CAPTURES")
    print("="*70)
    
    board = Board()
    
    print("\n1. STARTING POSITION:")
    print(f"White Material: {board.get_material_count(True)} points")
    print(f"Black Material: {board.get_material_count(False)} points")
    print(f"Imbalance: {board.material_imbalance():.1f} (balanced)")
    
    # Simulate a capture: 1.e4 e5 2.Nxe5 (knight takes pawn)
    board.make_move((6, 4), (4, 4))  # e2-e4
    board.turn = "black"
    board.make_move((1, 4), (3, 4))  # e7-e5
    board.turn = "white"
    board.make_move((7, 6), (5, 5))  # g1-f3
    board.turn = "black"
    board.make_move((0, 6), (2, 5))  # g8-f6
    board.turn = "white"
    board.make_move((5, 5), (3, 4))  # Nf3xe5
    
    print("\n2. AFTER KNIGHT CAPTURES PAWN (Nxe5):")
    print(f"White Material: {board.get_material_count(True)} points (gained 1 for pawn)")
    print(f"Black Material: {board.get_material_count(False)} points (lost 1 pawn)")
    print(f"Imbalance: {board.material_imbalance():.1f} (white +1)")

def demo_check_detection():
    """Demonstrate check detection."""
    print("\n" + "="*70)
    print("DEMO: CHECK DETECTION")
    print("="*70)
    
    board = Board()
    
    print("\n1. STARTING POSITION:")
    print(f"White in check: {board.is_in_check(True)} ✓")
    print(f"Black in check: {board.is_in_check(False)} ✓")
    
    # Set up a check position: Scholar's Mate setup (before checkmate)
    # 1.e4 e5 2.Bc4 Nc6 3.Qh5 Nf6 (Black to move, about to be checkmated)
    board = Board()
    moves = [
        ((6, 4), (4, 4), ""),  # e2-e4
        ((1, 4), (3, 4), ""),  # e7-e5
        ((7, 5), (5, 6), ""),  # f1-c4
        ((0, 1), (2, 2), ""),  # b8-c6
        ((7, 3), (5, 7), ""),  # d1-h5
        ((0, 6), (2, 5), ""),  # g8-f6
    ]
    
    for move, is_white in zip(moves, [True, False, True, False, True, False]):
        board.make_move(move[0], move[1], move[2])
        board.turn = "black" if board.turn == "white" else "white"
    
    print("\n2. AFTER 3...Nf6 (Scholar's Mate position):")
    board.print_board_simple()
    print(f"White in check: {board.is_in_check(True)}")
    print(f"Black in check: {board.is_in_check(False)} ← About to be checkmated!")
    
    # Execute checkmate
    board.make_move((5, 7), (1, 5))  # Qh5xf7#
    board.turn = "black"
    
    print("\n3. AFTER 4.Qxf7# (CHECKMATE!):")
    board.print_board_simple()
    print(f"Black in check: {board.is_in_check(False)} ← CHECKMATE!")

def demo_move_history():
    """Demonstrate move history tracking."""
    print("\n" + "="*70)
    print("DEMO: MOVE HISTORY TRACKING")
    print("="*70)
    
    board = Board()
    
    moves = [
        ((6, 4), (4, 4), ""),  # e2-e4
        ((1, 4), (3, 4), ""),  # e7-e5
        ((7, 6), (5, 5), ""),  # g1-f3
    ]
    
    print("\nMaking moves and tracking history:\n")
    
    for idx, (move, is_white) in enumerate(zip(moves, [True, False, True]), 1):
        board.make_move(move[0], move[1], move[2])
        from_sq = board.square_to_algebraic(move[0][0], move[0][1])
        to_sq = board.square_to_algebraic(move[1][0], move[1][1])
        player = "White" if is_white else "Black"
        print(f"{idx}. {player}: {from_sq} → {to_sq}")
        print(f"   - Move count: {board.get_move_count()}")
        print(f"   - Last move: {board.get_last_move()}\n")
        board.turn = "black" if board.turn == "white" else "white"

if __name__ == "__main__":
    print("\n" + "█"*70)
    print("█" + " "*68 + "█")
    print("█" + "  CHESS BOARD UI DEMO - IMPROVED FEATURES".center(68) + "█")
    print("█" + " "*68 + "█")
    print("█"*70)
    
    demo_board_display()
    demo_algebraic_notation()
    demo_position_evaluation()
    demo_move_suggestions()
    demo_material_tracking()
    demo_check_detection()
    demo_move_history()
    
    print("\n" + "█"*70)
    print("█" + "  All demos completed!".center(68) + "█")
    print("█"*70 + "\n")
