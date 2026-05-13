#!/usr/bin/env python3
"""
Test script to demonstrate board.py improvements:
- Chess.com style board representation with Unicode pieces
- Check detection
- Material count tracking
- Move history
"""

from engine.board import Board

def test_board_display():
    """Test the new chess.com style board display."""
    print("=" * 60)
    print("BOARD DISPLAY TEST - Chess.com Style with Unicode Pieces")
    print("=" * 60)
    board = Board()
    board.print_board()

def test_material_count():
    """Test material counting feature."""
    print("=" * 60)
    print("MATERIAL COUNT TEST")
    print("=" * 60)
    board = Board()
    white_material = board.get_material_count(is_white=True)
    black_material = board.get_material_count(is_white=False)
    imbalance = board.material_imbalance()
    
    print(f"White material: {white_material}")
    print(f"Black material: {black_material}")
    print(f"Material imbalance (white advantage): {imbalance}")
    print()

def test_check_detection():
    """Test check detection with Scholar's mate setup."""
    print("=" * 60)
    print("CHECK DETECTION TEST - Scholar's Mate Setup")
    print("=" * 60)
    
    # Setup Scholar's mate position (white to move for checkmate)
    board = Board()
    
    # 1. e4 e5
    board.make_move((6, 4), (4, 4))  # e2-e4
    board.turn = "black"
    board.make_move((1, 4), (3, 4))  # e7-e5
    board.turn = "white"
    
    # 2. Bc4 Nc6
    board.make_move((7, 5), (5, 6))  # f1-c4
    board.turn = "black"
    board.make_move((0, 1), (2, 2))  # b8-c6
    board.turn = "white"
    
    # 3. Qh5 Nf6??
    board.make_move((7, 3), (5, 7))  # d1-h5
    board.turn = "black"
    board.make_move((0, 6), (2, 5))  # g8-f6
    board.turn = "white"
    
    print("Position after 3...Nf6?? (Black is about to be checkmated):")
    board.print_board()
    
    # 4. Qxf7# - Checkmate!
    board.make_move((5, 7), (1, 5))  # h5-f7
    board.turn = "black"
    
    print("Position after 4. Qxf7# - CHECKMATE!")
    board.print_board()
    
    is_check = board.is_in_check(is_white=False)
    print(f"Black in check: {is_check}")
    print()

def test_move_history():
    """Test move history tracking."""
    print("=" * 60)
    print("MOVE HISTORY TEST")
    print("=" * 60)
    
    board = Board()
    
    # Make some moves
    moves = [
        ((6, 4), (4, 4), None),  # e2-e4
        ((1, 4), (3, 4), None),  # e7-e5
        ((6, 1), (5, 3), None),  # g2-f3
        ((0, 1), (2, 2), None),  # b8-c6
    ]
    
    for start, end, promo in moves:
        board.make_move(start, end, promo)
        board.turn = "black" if board.turn == "white" else "white"
    
    print(f"Total moves made: {board.get_move_count()}")
    print(f"Last move: {board.get_last_move()}")
    print()

def test_piece_values():
    """Test piece value system."""
    print("=" * 60)
    print("PIECE VALUE TEST")
    print("=" * 60)
    
    pieces = {'P': 'Pawn', 'N': 'Knight', 'B': 'Bishop', 'R': 'Rook', 'Q': 'Queen', 'K': 'King'}
    
    for piece, name in pieces.items():
        value = Board.get_piece_value(piece)
        print(f"{name:8} ({piece}): {value} points")
    print()

def test_square_attacked():
    """Test square attacked detection."""
    print("=" * 60)
    print("SQUARE ATTACKED DETECTION TEST")
    print("=" * 60)
    
    board = Board()
    
    # Check if white pawns attack key squares after 1.e4
    board.make_move((6, 4), (4, 4))  # e2-e4
    board.turn = "white"
    
    print("After 1. e4, squares attacked by white:")
    
    # Check several squares
    check_squares = [
        (3, 3, "d5"),
        (3, 5, "f5"),
        (4, 3, "d4"),
        (4, 5, "f4"),
    ]
    
    for row, col, name in check_squares:
        attacked = board.is_square_attacked(row, col, by_white=True)
        print(f"  {name}: {'Yes' if attacked else 'No'}")
    print()

def test_encoding():
    """Test move encoding/decoding."""
    print("=" * 60)
    print("MOVE ENCODING TEST")
    print("=" * 60)
    
    start = (6, 4)  # e2
    end = (4, 4)    # e4
    promo = ""
    
    encoded = Board.encode_move(start, end, promo)
    decoded = Board.decode_move(encoded)
    
    print(f"Original:  start={start}, end={end}, promo='{promo}'")
    print(f"Encoded:   {encoded}")
    print(f"Decoded:   start={decoded[0]}, end={decoded[1]}, promo='{decoded[2]}'")
    print()

if __name__ == "__main__":
    test_board_display()
    test_material_count()
    test_move_history()
    test_piece_values()
    test_square_attacked()
    test_encoding()
    test_check_detection()
    
    print("=" * 60)
    print("All tests completed!")
    print("=" * 60)
