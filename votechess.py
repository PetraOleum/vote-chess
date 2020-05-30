import chess
import chess.engine
import chess.svg
import chess.pgn
from random import shuffle, sample, seed
from cairosvg import svg2png
import datetime
import os
from mastodon import Mastodon
from time import sleep
import argparse

parser = argparse.ArgumentParser(description="Vote chess mastodon bot")
parser.add_argument("--human-depth", type=int, default=10,
                    help="Depth to search when identifying human moves",
                    dest="hdist")
parser.add_argument("--engine-depth", type=int, default=10,
                    help="Depth to search when identifying engine moves",
                    dest="edist")
parser.add_argument("-d", "--dir", default=".", dest="dir",
                    help="Directory to operate in")
parser.add_argument("-p", "--poll-length", default=3540, type=int,
                    dest="poll_length",
                    help="Number of seconds for poll to last")

args = parser.parse_args()
os.chdir(args.dir)

mastodon = Mastodon(
    access_token = 'votechess_usercred.secret',
    api_base_url = 'https://botsin.space'
)


seed()
book = ["e2e4", "d2d4", "g1f3", "c2c4", "g2g3"]
lastMove = None
lastHuman = None
limithuman = chess.engine.Limit(depth=args.hdist)
limitengine = chess.engine.Limit(depth=args.edist)
player = chess.WHITE
lasttoot_id = None

def print_board(board):
    global player
    lm = None
    if len(board.move_stack) > 0:
        lm = board.peek()
    board_svg = chess.svg.board(board, flipped = (player == chess.BLACK),
                                lastmove=lm)
    svg2png(bytestring=board_svg,write_to='cur.png')


def clean_endgame(board, lastMove, lastMbut1 = None):
    global player
    global mastodon
    global lasttoot_id
    print_board(board)
    img = mastodon.media_post("cur.png", description=
                              "Position after {}\nFEN: {}".format(
                                  lastMove, board.fen()))
    res = board.result(claim_draw=False)
    egmsg = ""
    if board.is_checkmate():
        egmsg = "Checkmate!\n"
        if lastMbut1 == None:
            egmsg = egmsg + "With {} the humans win!\n".format(lastMove)
        else:
            egmsg = egmsg + "The computer replies to {} with {}\n".format(lastMbut1,
                                                              lastMove)
    elif board.is_stalemate():
        if lastMbut1 == None:
            egmsg = egmsg + "With {} the humans stalemate the computer!\n".format(lastMove)
        else:
            egmsg = egmsg + "The computer replies to {} with {}. Stalemate!\n".format(
                lastMbut1, lastMove)
    else:
        if lastMbut1 == None:
            egmsg = egmsg + "With {} the humans claim a draw.\n".format(lastMove)
        else:
            egmsg = egmsg + "The computer replies to {} with {}, and claims a draw.".format(
                lastMbut1, lastMove)

    pgn = chess.pgn.Game.from_board(board)
    pgn.headers["Result"] = res
    egmsg = egmsg + res
    # Keep going tomorrow
    pgn.headers["Date"] = datetime.date.today().strftime("%Y.%m.%d")
    pgn.headers["Site"] = "@votechess@botsin.space"
    pgn.headers["Event"] = "Mastodon vote chess"
    if player == chess.WHITE:
        pgn.headers["White"] = "Mastodon"
        pgn.headers["Black"] = "Computer"
    else:
        pgn.headers["White"] = "Computer"
        pgn.headers["Black"] = "Mastodon"
    print(egmsg)
    lasttoot_id = mastodon.status_post(egmsg,
                                       in_reply_to_id=None,
                                       media_ids=img,
                                       visibility="public")["id"]
    print(pgn, file=open("archive.pgn", "a"), end="\n\n")
    os.remove("current.pgn")


def eng_rate(legmoves, board, engine):
    global limithuman
    moves = list()
    for mv in legmoves:
        board.push(mv)
        sc = engine.analyse(board, limithuman)["score"]
        board.pop()
        moves.append((mv, sc.relative))
    moves.sort(key = lambda tup: tup[1])
    return moves


def eng_choose(legmoves, board):
    engine = chess.engine.SimpleEngine.popen_uci("stockfish")
    moves = eng_rate(legmoves, board, engine)
    engine.quit()
    return moves[0][0]


def set_up_vote(last_Comp_Move, curBoard, lastHuman=None):
    global mastodon
    global lasttoot_id
    global args
    print_board(curBoard)
    img = mastodon.media_post("cur.png", description=
                              "Position after {}\nFEN: {}".format(
                                  last_Comp_Move, curBoard.fen()))
    engine = chess.engine.SimpleEngine.popen_uci("stockfish")
    moves = eng_rate(curBoard.legal_moves, curBoard, engine)
    engine.quit()
    if len(moves) < 5:
        options = moves
    else:
        options = moves[:3]
        options.extend(moves[-1:])
    shuffle(options)

    tootstring = ""

    # For now, just print to stdout
    if lastHuman == None:
        tootstring = "New Game\n"
    else:
        tootstring = "Poll result: {}\n".format(lastHuman)
    if last_Comp_Move != None:
        tootstring = tootstring + "Computer move: {}".format(last_Comp_Move)

    print(tootstring)

    lasttoot_id = mastodon.status_post(tootstring,
                                       in_reply_to_id=None,
                                       media_ids=img,
                                       visibility="public")["id"]
    sleep(50)
    tootstring = ""
    if len(options) == 1:
        tootstring = "Only one legal move: {}".format(
              board.variation_san([options[0][0]]))
        lasttoot_id = mastodon.status_post(tootstring,
                                           in_reply_to_id=lasttoot_id,
                                           visibility="public")["id"]
    else:
        tootstring = "Options:\n"
        for i in range(len(options)):
            tootstring = tootstring + "{}) {}\n".format(i+1, curBoard.variation_san([options[i][0]]))
        opstrings = [curBoard.san(mv[0]) for mv in options]
        poll = mastodon.make_poll(opstrings, expires_in = args.poll_length)
        if last_Comp_Move == None:
            tmsg = "Choose a move to play:"
        else:
            tmsg = ("Choose a move to reply to {}:").format(last_Comp_Move)

        lasttoot_id = mastodon.status_post(tmsg, poll=poll,
                                           in_reply_to_id=lasttoot_id,
                                           visibility="public")["id"]
        print(lasttoot_id, file=open("lastpost.id", "w"))


def get_vote_results(curBoard):
    global book
    global lasttoot_id
    global mastodon
    # For now, just select best move
    try:
        print(lasttoot_id)
        poll = mastodon.status(id = lasttoot_id)["poll"]
        print("Got poll")
        votes = [mv["votes_count"] for mv in poll["options"]]
        mvotes = max(votes)
        choices = [curBoard.parse_san(mv["title"]) for mv in poll["options"] if
                   mv["votes_count"] == mvotes]
        return eng_choose(choices, curBoard)
    except Exception as e:
        print("Failed to get poll results")
        print(e)
        if len(curBoard.move_stack) > 0:
            return eng_choose(curBoard.legal_moves, curBoard)
        else:
            return chess.Move.from_uci(sample(book, 1)[0])



def load_game():
    global player
    global lastMove
    global book
    global lasttoot_id
    # 1. Test if game exists, is not ended
    newGame = False
    try:
        with open("lastpost.id", "r") as idfile:
            lasttoot_id = idfile.read()
    except:
        lasttoot_id = None

    try:
        pgn = open("current.pgn")
        curGame = chess.pgn.read_game(pgn)
        board = curGame.end().board()
        # If exists but is ended, archive, continue
        if board.is_game_over(claim_draw=False):
            newGame = True
            print(curGame, file=open("archive.pgn", "a"), end="\n\n")
            os.remove("current.pgn")
        else:
            player = board.turn
            lastMove = None
            if len(board.move_stack) > 0:
                lastMove = board.peek()
    except:
        newGame = True
    # Create new game
    if newGame:
        lasttoot_id = None
        lastMove = None
        board = chess.Board()
        player = sample([chess.WHITE, chess.BLACK], 1)[0]
        lastMoveSan = None
        if player == chess.BLACK:
            lastMove = chess.Move.from_uci(sample(book, 1)[0])
            lastMoveSan = board.variation_san([lastMove])
            board.push(lastMove)
        set_up_vote(lastMoveSan, board, None)
        pgn = chess.pgn.Game.from_board(board)
        pgn.headers["Result"] = "*"
        pgn.headers["Date"] = datetime.date.today().strftime("%Y.%m.%d")
        pgn.headers["Site"] = "@votechess@botsin.space"
        pgn.headers["Event"] = "Mastodon vote chess"
        if player == chess.WHITE:
            pgn.headers["White"] = "Mastodon"
            pgn.headers["Black"] = "Computer"
        else:
            pgn.headers["White"] = "Computer"
            pgn.headers["Black"] = "Mastodon"
        print(pgn)
        print(pgn, file=open("current.pgn", "w"), end="\n\n")
        quit()
    return board


board = load_game()
legmovs = list(board.legal_moves)
# 3. If only one legal move, just make it
if len(legmovs) == 1:
    humMove = legmovs[0]
else:
    # 4. Otherwise, gather results from thread for game. If tie, break with engine analysis, make move
    humMove = get_vote_results(board)
humMoveSan = board.variation_san([humMove])
board.push(humMove)

if not board.is_game_over(claim_draw=False):
    # 6. Make engine move
    # legmovs = list(board.legal_moves)
    # engmov = eng_choose()
    engine = chess.engine.SimpleEngine.popen_uci("stockfish")
    engmov = engine.play(board, limitengine).move
    engine.quit()
    lastMove = engmov
    lastMoveSan = board.variation_san([lastMove])
    board.push(engmov)
    if not board.is_game_over(claim_draw=False):
        set_up_vote(lastMoveSan, board, humMoveSan)
        # Save board
        print("saving")
        pgn = chess.pgn.Game.from_board(board)
        pgn.headers["Result"] = "*"
        pgn.headers["Date"] = datetime.date.today().strftime("%Y.%m.%d")
        pgn.headers["Site"] = "@votechess@botsin.space"
        pgn.headers["Event"] = "Mastodon vote chess"
        if player == chess.WHITE:
            pgn.headers["White"] = "Mastodon"
            pgn.headers["Black"] = "Computer"
        else:
            pgn.headers["White"] = "Computer"
            pgn.headers["Black"] = "Mastodon"
        # print(pgn)
        print(pgn, file=open("current.pgn", "w"), end="\n\n")
    else:
        clean_endgame(board, lastMoveSan, humMoveSan)
else:
    clean_endgame(board, humMoveSan, None)






