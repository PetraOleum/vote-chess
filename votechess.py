import chess
import chess.engine
import chess.svg
import chess.pgn
import chess.polyglot
from random import shuffle, sample, seed
from numpy.random import choice
from cairosvg import svg2png
import datetime
import os
from mastodon import Mastodon
from time import sleep
import argparse
import requests

parser = argparse.ArgumentParser(description="Vote chess mastodon bot")
parser.add_argument("--human-depth", type=int, default=10,
                    help="Depth to search when identifying human moves",
                    dest="hdist")
parser.add_argument("--debug", dest="debug", action="store_true",
                    help="do not post to mastodon, do print boards")
parser.add_argument("--engine-depth", type=int, default=10,
                    help="Depth to search when identifying engine moves",
                    dest="edist")
parser.add_argument("-d", "--dir", default=".", dest="dir",
                    help="Directory to operate in")
parser.add_argument("-p", "--poll-length", default=3420, type=int,
                    dest="poll_length",
                    help="Number of seconds for poll to last")
parser.add_argument("--polyglot", default="",
                    dest="polyglot_book",
                    help="Polyglot opening book")

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

def opening_choice(board, bookfile, k=1):
    if k < 1:
        return [None]
    bweights = []
    bmoves = []
    try:
        with chess.polyglot.open_reader(bookfile) as reader:
            allbook = [e for e in reader.find_all(board)]
            bweights = [e.weight for e in allbook]
            bmoves = [e.move for e in allbook]
            print("Opening book:")
            for entry in reader.find_all(board):
                print(entry.move, entry.weight, entry.learn)
        if len(bmoves) > k:
            sw = sum(bweights)
            pweights = [b / sw for b in bweights]
            chosen = choice(bmoves, k, replace = False, p=pweights)
            return chosen
        if len(bmoves) > 0:
            return(bmoves)
    except Exception as e:
        print("Failed to read opening book")
        print(e)
    return [None]

def print_board(board):
    global player
    lm = None
    if len(board.move_stack) > 0:
        lm = board.peek()
    board_svg = chess.svg.board(board, flipped = (player == chess.BLACK),
                                lastmove=lm)
    svg2png(bytestring=board_svg,write_to='cur.png', scale=1.5)


def clean_endgame(board, lastMove, lastMbut1 = None, adjud = False):
    global player
    global mastodon
    global lasttoot_id
    global args
    print_board(board)
    if not args.debug:
        img = mastodon.media_post("cur.png", description=
                                  "Position after {}\nFEN: {}".format(
                                      lastMove, board.fen()))
    res = board.result(claim_draw=False)
    if adjud:
        res = "1/2-1/2"
    egmsg = ""
    if lastMove == "resignation":
        egmsg = "The humans resign!"
        if board.turn == chess.WHITE:
            res = "0-1"
        else:
            res = "1-0"
    elif board.is_checkmate():
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
    elif adjud:
        if lastMbut1 == None:
            egmsg = egmsg + ("After {} the position is adjudicated to a "
                             "tablebase draw.\n").format(lastMove)
        else:
            egmsg = egmsg + "The computer replies to {} with {}.\n".format(
                lastMbut1, lastMove)
            egmsg = egmsg + "The position is adjudicated to a tablebase draw.\n"
        fenmod = board.fen().replace(" ", "_")
        egmsg = egmsg + "https://syzygy-tables.info/?fen={}\n".format(fenmod)
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
        pgn.headers["Black"] = "Computer (depth {})".format(args.edist)
    else:
        pgn.headers["White"] = "Computer (depth {})".format(args.edist)
        pgn.headers["Black"] = "Mastodon"
    print(egmsg)
    if args.debug:
        print(egmsg)
        print(board)
    else:
        lasttoot_id = mastodon.status_post(egmsg,
                                           in_reply_to_id=lasttoot_id,
                                           media_ids=img,
                                           visibility="public")["id"]
        print(pgn, file=open("archive.pgn", "a"), end="\n\n")
        os.remove("current.pgn")


def eng_rate(legmoves, board, engine, lim):
    moves = list()
    for mv in legmoves:
        if bool(mv):
            board.push(mv)
            sc = engine.analyse(board, lim)["score"]
            board.pop()
            moves.append((mv, sc.relative))
        else:
            moves.append((mv, chess.engine.MateGiven))
    moves.sort(key = lambda tup: tup[1])
    return moves


def eng_choose(legmoves, board, lim):
    # global args
    # if board.fullmove_number < 10 and args.polyglot_book != "":
    #     opc = opening_choice(board, args.polyglot_book)
    #     if opc is not None:
    #         return opc

    engine = chess.engine.SimpleEngine.popen_uci("stockfish")
    moves = eng_rate(legmoves, board, engine, lim)
    engine.quit()
    return moves[0][0]


def set_up_vote(last_Comp_Move, curBoard, lastHuman=None):
    global mastodon
    global lasttoot_id
    global args
    global limithuman
    print_board(curBoard)
    if not args.debug:
        img = mastodon.media_post("cur.png", description=
                                  "Position after {}\nFEN: {}".format(
                                      last_Comp_Move, curBoard.fen()))
    curlegmoves = [m for m in curBoard.legal_moves]
    moves = []
    if curBoard.fullmove_number < 10 and args.polyglot_book != "":
        moves = [m for m in opening_choice(curBoard, args.polyglot_book, 4)]
        if moves[0] is None:
            moves = []
    if len(moves) < len(curlegmoves) and len(moves) < 5:
        engine = chess.engine.SimpleEngine.popen_uci("stockfish")
        emovs = eng_rate([mov for mov in curlegmoves if mov not in moves], curBoard, engine, limithuman)
        moves = moves + [m[0] for m in emovs]
        engine.quit()
        # resign if more than 5 pawn-equivalents down
        if emovs[0][1] > chess.engine.Cp(-500):
            moves = [chess.Move.null()] + moves
    if len(moves) < 5:
        options = moves
    else:
        options = moves[:4] # Get four best, not top 3 and bottom 1
        # options = moves[:3]
        # options.extend(moves[-1:])
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

    if args.debug:
        print(curBoard)
    else:
        lasttoot_id = mastodon.status_post(tootstring,
                                           in_reply_to_id=lasttoot_id,
                                           media_ids=img,
                                           visibility="public")["id"]
        sleep(50)
    tootstring = ""
    if len(options) == 1:
        tootstring = "Only one legal move: {}".format(
              board.variation_san([options[0]]))
        if not args.debug:
            lasttoot_id = mastodon.status_post(tootstring,
                                               in_reply_to_id=lasttoot_id,
                                               visibility="public")["id"]
            print(lasttoot_id, file=open("lastpost.id", "w"))
        else:
            print(tootstring)
    else:
        tootstring = "Options:\n"
        for i in range(len(options)):
            tootstring = tootstring + "{}) {}\n".format(i+1, curBoard.variation_san([options[i]]) if
                              bool(options[i]) else "Resign")
        opstrings = [(curBoard.san(mv) if bool(mv) else "Resign") for mv in options]
        if not args.debug:
            poll = mastodon.make_poll(opstrings, expires_in = args.poll_length)

        if last_Comp_Move == None:
            tmsg = "Choose a move to play:"
        else:
            tmsg = ("Choose a move to reply to {}:").format(last_Comp_Move)

        if not args.debug:
            lasttoot_id = mastodon.status_post(tmsg, poll=poll,
                                               in_reply_to_id=lasttoot_id,
                                               visibility="public")["id"]
            print(lasttoot_id, file=open("lastpost.id", "w"))
        else:
            print(tmsg)
            print(tootstring)


def get_vote_results(curBoard):
    global lasttoot_id
    global mastodon
    global limitengine
    # For now, just select best move
    try:
        print(lasttoot_id)
        poll = mastodon.status(id = lasttoot_id)["poll"]
        print("Got poll")
        votes = [mv["votes_count"] for mv in poll["options"]]
        mvotes = max(votes)
        choices = [(curBoard.parse_san(mv["title"]) if mv["title"] != "Resign"
                   else chess.Move.null()) for mv in poll["options"] if
                   mv["votes_count"] == mvotes]
        return eng_choose(choices, curBoard, limitengine)
    except Exception as e:
        print("Failed to get poll results")
        print(e)
        return eng_choose(curBoard.legal_moves, curBoard, limitengine)



def load_game():
    global player
    global lastMove
    global book
    global lasttoot_id
    global args
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
            if not args.debug:
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
            lastMove = None
            if args.polyglot_book != "":
                lastMove = opening_choice(board, args.polyglot_book)[0]
            if lastMove is None:
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
            pgn.headers["Black"] = "Computer (depth {})".format(args.edist)
        else:
            pgn.headers["White"] = "Computer (depth {})".format(args.edist)
            pgn.headers["Black"] = "Mastodon"
        print(pgn)
        if not args.debug:
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

if bool(humMove):
    humMoveSan = board.variation_san([humMove])
    board.push(humMove)
else:
    humMoveSan = "resignation"

if not board.is_game_over(claim_draw=False) and bool(humMove):
    # 6. Make engine move
    # legmovs = list(board.legal_moves)
    # engmov = eng_choose()
    engmov = None
    if board.fullmove_number < 10 and args.polyglot_book != "":
        engmov = opening_choice(board, args.polyglot_book)[0]

    if engmov is None:
        engine = chess.engine.SimpleEngine.popen_uci("stockfish")
        engmov = engine.play(board, limitengine).move
        engine.quit()

    lastMove = engmov
    lastMoveSan = board.variation_san([lastMove])
    board.push(engmov)
    if not board.is_game_over(claim_draw=False):
        if board.halfmove_clock > 20 or board.halfmove_clock < 2:
            if len(board.piece_map()) < 8:
                fenmod = board.fen().replace(" ", "_")
                apiurl = "http://tablebase.lichess.ovh/standard?fen="
                r = requests.get(url="{}{}".format(apiurl, fenmod))
                if r.json()["wdl"] == 0:
                    print("Tablebase draw")
                    clean_endgame(board, lastMoveSan, humMoveSan, True)
                    quit()
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
        if not args.debug:
            print(pgn, file=open("current.pgn", "w"), end="\n\n")
    else:
        clean_endgame(board, lastMoveSan, humMoveSan)
else:
    clean_endgame(board, humMoveSan, None)







