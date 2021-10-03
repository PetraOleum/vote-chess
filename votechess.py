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
import json
import io

parser = argparse.ArgumentParser(description="Vote chess mastodon bot")
parser.add_argument(help="Config filepath (json)",
                    dest="config_file")
parser.add_argument("--debug", dest="debug", action="store_true",
                    help="do not post to mastodon, do print boards")
parser.add_argument("--human-depth", type=int,
                    help="Depth to search when identifying human moves",
                    dest="hdist")
parser.add_argument("--engine-depth", type=int,
                    help="Depth to search when identifying engine moves",
                    dest="edist")
parser.add_argument("-d", "--dir", default=".", dest="dir",
                    help="Directory to operate in")
parser.add_argument("--polyglot",
                    dest="polyglot_book",
                    help="Polyglot opening book")

args = parser.parse_args()
os.chdir(args.dir)

mastodon = Mastodon(
    access_token = 'votechess_usercred.secret',
    api_base_url = 'https://botsin.space'
)

configfp = args.config_file

with open(configfp, "r") as configfile:
    config = json.load(configfile)

seed()
book = ["e2e4", "d2d4", "g1f3", "c2c4", "g2g3"]
lastMove = None
lastHuman = None
limithuman = chess.engine.Limit(depth=config["human"].get("depth"))
limitengine = chess.engine.Limit(depth=config["engine"].get("depth"))
player = chess.WHITE
lasttoot_id = None


def save_config():
    global config
    global args
    with open(args.config_file, "w") as configfile:
        json.dump(config, configfile, indent=2)


def pgn_standard_headers(pgn, player):
    global config
    pgn.headers["Date"] = datetime.date.today().strftime("%Y.%m.%d")
    if config.get("site") is not None:
        pgn.headers["Site"] = config.get("site")
    pgn.headers["Event"] = "Vote chess: {}".format(config["name"])
    if player == chess.WHITE:
        pgn.headers["White"] = config["human"].get("name")
        pgn.headers["Black"] = "{} (depth {})".format(
            config["engine"].get("name"), config["engine"].get("depth"))
    else:
        pgn.headers["White"] = "{} (depth {})".format(
            config["engine"].get("name"), config["engine"].get("depth"))
        pgn.headers["Black"] = config["human"].get("name")
    pgn.headers["Round"] = config.get("round")
    return pgn


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

def print_board(board, choices = None):
    global player
    global config
    lm = None
    arrows = [] if choices is None or config.get("show_arrows") != True else [(move.from_square, move.to_square)
                                           for move in choices if move != chess.Move.from_uci("0000")]
    if len(board.move_stack) > 0:
        lm = board.peek()
    board_svg = chess.svg.board(board, flipped = (player == chess.BLACK),
                                lastmove=lm, colors =
                                config.get("board_colours"), arrows=arrows)
    svg2png(bytestring=board_svg,write_to=config.get("image_file"), scale=1.5)


def clean_endgame(board, lastMove, lastMbut1 = None, adjud = False):
    global player
    global mastodon
    global lasttoot_id
    global args
    global config
    print_board(board)
    if not args.debug:
        img = mastodon.media_post(config.get("image_file"), description=
                                  "Position after {}\nFEN: {}".format(
                                      lastMove, board.fen()))
    res = board.result(claim_draw=False)
    if adjud:
        res = "1/2-1/2"
        config["human"]["score"] = config["human"]["score"] + 0.5
        config["engine"]["score"] = config["engine"]["score"] + 0.5
    egmsg = ""
    if lastMove == "resignation":
        egmsg = "The humans resign!\n"
        config["engine"]["score"] = config["engine"]["score"] + 1.0
        if board.turn == chess.WHITE:
            res = "0-1"
        else:
            res = "1-0"
    elif board.is_checkmate():
        egmsg = "Checkmate!\n"
        if lastMbut1 == None:
            egmsg = egmsg + "With {} the humans win!\n".format(lastMove)
            config["human"]["score"] = config["human"]["score"] + 1.0
        else:
            egmsg = egmsg + "The computer replies to {} with {}\n".format(lastMbut1,
                                                              lastMove)
            config["engine"]["score"] = config["engine"]["score"] + 1.0
    elif board.is_stalemate():
        config["human"]["score"] = config["human"]["score"] + 0.5
        config["engine"]["score"] = config["engine"]["score"] + 0.5
        if lastMbut1 == None:
            egmsg = egmsg + "With {} the humans stalemate the computer!\n".format(lastMove)
        else:
            egmsg = egmsg + "The computer replies to {} with {}. Stalemate!\n".format(
                lastMbut1, lastMove)
    elif adjud:
        config["human"]["score"] = config["human"]["score"] + 0.5
        config["engine"]["score"] = config["engine"]["score"] + 0.5
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
        config["human"]["score"] = config["human"]["score"] + 0.5
        config["engine"]["score"] = config["engine"]["score"] + 0.5
        if lastMbut1 == None:
            egmsg = egmsg + "With {} the humans claim a draw.\n".format(lastMove)
        else:
            egmsg = egmsg + "The computer replies to {} with {}, and claims a draw.\n".format(
                lastMbut1, lastMove)

    pgn = chess.pgn.Game.from_board(board)
    pgn.headers["Result"] = res
    #egmsg = egmsg + res
    egmsg = egmsg + ("Current score: {} {}, {} {}").format(
        config["human"]["name"], config["human"]["score"],
        config["engine"]["name"], config["engine"]["score"])
    pgn = pgn_standard_headers(pgn, player)
    print(egmsg)
    if args.debug:
        print(board)
        config["pgn"] = None
    else:
        lasttoot_id = mastodon.status_post(egmsg,
                                           in_reply_to_id=lasttoot_id,
                                           media_ids=img,
                                           visibility="public")["id"]
        config["postid"] = lasttoot_id
        if config.get("archive_file") is not None:
            print(pgn, file=open(config.get("archive.file"), "a"), end="\n\n")
        config["pgn"] = None
    config["human"]["colour"] = "WHITE" if config["human"].get("colour") == "BLACK" else "BLACK"
    if args.hdist is not None:
        config["human"]["depth"] = args.hdist
    if args.edist is not None:
        config["engine"]["depth"] = args.edist
    if args.polyglot_book is not None:
        config["polyglot_book"] = args.polyglot_book
    config["poll_options"] = None
    config["round"] = config.get("round") + 1
    save_config()


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
    global config
    # global args
    # if board.fullmove_number < 10 and args.polyglot_book != "":
    #     opc = opening_choice(board, args.polyglot_book)
    #     if opc is not None:
    #         return opc

    engine = chess.engine.SimpleEngine.popen_uci(config["engine"].get("path"))
    moves = eng_rate(legmoves, board, engine, lim)
    engine.quit()
    return moves[0][0]


def set_up_vote(last_Comp_Move, curBoard, lastHuman=None):
    global mastodon
    global lasttoot_id
    global args
    global config
    global limithuman
    curlegmoves = [m for m in curBoard.legal_moves]
    moves = []
    if curBoard.fullmove_number < 10 and config.get("polyglot_book") is not None:
        moves = [m for m in opening_choice(curBoard, config["polyglot_book"], 4)]
        if moves[0] is None:
            moves = []
    if len(moves) < len(curlegmoves) and len(moves) < 5:
        engine = chess.engine.SimpleEngine.popen_uci(config["engine"].get("path"))
        emovs = eng_rate([mov for mov in curlegmoves if mov not in moves], curBoard, engine, limithuman)
        moves = moves + [m[0] for m in emovs]
        engine.quit()
        # resign if more than 5 pawn-equivalents down
        if emovs[0][1] > chess.engine.Cp(500):
            moves = [chess.Move.null()] + moves
    if len(moves) < 5:
        options = moves
    else:
        options = moves[:4] # Get four best, not top 3 and bottom 1
        # options = moves[:3]
        # options.extend(moves[-1:])
    shuffle(options)
    print_board(curBoard, choices=options)
    if not args.debug:
        img = mastodon.media_post(config.get("image_file"), description=
                                  "Position after {}\nFEN: {}".format(
                                      last_Comp_Move, curBoard.fen()))

    tootstring = ""

    # For now, just print to stdout
    if lastHuman == None:
        tootstring = "New Game vs {}\n".format(config["engine"].get("name"))
    else:
        tootstring = "Poll result: {}\n".format(lastHuman)
    if last_Comp_Move != None:
        tootstring = tootstring + "Computer move from {}: {}".format(
            config["engine"].get("name"), last_Comp_Move)

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
            config["postid"] = lasttoot_id
        else:
            print(tootstring)
            config["postid"] = None
        config["poll_options"] = None
    else:
        tootstring = "Options:\n"
        for i in range(len(options)):
            tootstring = tootstring + "{}) {}\n".format(i+1, curBoard.variation_san([options[i]]) if
                              bool(options[i]) else "Resign")
        opstrings = [(curBoard.san(mv) if bool(mv) else "Resign") for mv in options]
        config["poll_options"] = opstrings
        if not args.debug:
            poll = mastodon.make_poll(opstrings, expires_in = config.get("poll_length"))

        if last_Comp_Move == None:
            tmsg = "Choose a move to play:"
        else:
            tmsg = ("Choose a move to reply to {}:").format(last_Comp_Move)

        if not args.debug:
            lasttoot_id = mastodon.status_post(tmsg, poll=poll,
                                               in_reply_to_id=lasttoot_id,
                                               visibility="public")["id"]
            config["postid"] = lasttoot_id
        else:
            print(tmsg)
            print(tootstring)
            config["postid"] = None


def get_vote_results(curBoard):
    global lasttoot_id
    global mastodon
    global limitengine
    # For now, just select best move
    if lasttoot_id is None:
        print("No poll")
        return eng_choose(curBoard.legal_moves, curBoard, limitengine)
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
    global config
    # 1. Test if game exists, is not ended
    newGame = False
    lasttoot_id = config.get("postid")
    try:
        pgn = io.StringIO(config.get("pgn"))
        curGame = chess.pgn.read_game(pgn)
        board = curGame.end().board()
        # If exists but is ended, archive, continue
        if board.is_game_over(claim_draw=False):
            newGame = True
            if not args.debug:
                if config.get("archive_file") is not None:
                    print(curGame, file=open(config.get("archive_file"), "a"), end="\n\n")
            config["pgn"] = None
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
        config["postid"] = None
        lastMove = None
        board = chess.Board()
        player = chess.BLACK if config["human"].get("colour") == "BLACK" else chess.WHITE
        lastMoveSan = None
        if player == chess.BLACK:
            lastMove = None
            if config.get("polyglot_book") is not None:
                lastMove = opening_choice(board, config.get("polyglot_book"))[0]
            if lastMove is None:
                lastMove = chess.Move.from_uci(sample(book, 1)[0])
            lastMoveSan = board.variation_san([lastMove])
            board.push(lastMove)
        set_up_vote(lastMoveSan, board, None)
        pgn = chess.pgn.Game.from_board(board)
        pgn.headers["Result"] = "*"
        pgn = pgn_standard_headers(pgn, player)
        print(pgn)
        config["pgn"] = str(pgn)
        save_config()
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
    if board.fullmove_number < 10 and config.get("polyglot_book") is not None:
        engmov = opening_choice(board, config.get("polyglot_book"))[0]

    if engmov is None:
        engine = chess.engine.SimpleEngine.popen_uci(config["engine"].get("path"))
        engmov = engine.play(board, limitengine).move
        engine.quit()

    lastMove = engmov
    lastMoveSan = board.variation_san([lastMove])
    board.push(engmov)
    if not board.is_game_over(claim_draw=False):
        try:
            if board.halfmove_clock > 20 or board.halfmove_clock < 2:
                if len(board.piece_map()) < 8:
                    fenmod = board.fen().replace(" ", "_")
                    apiurl = "http://tablebase.lichess.ovh/standard?fen="
                    r = requests.get(url="{}{}".format(apiurl, fenmod))
                    res = r.json()
                    if res.get("wdl") == 0 or res.get("category") == "draw":
                        print("Tablebase draw")
                        clean_endgame(board, lastMoveSan, humMoveSan, True)
                        quit()
        except Exception as e:
            print("Failed to get adjudication")
            print(e)
        set_up_vote(lastMoveSan, board, humMoveSan)
        # Save board
        print("saving")
        pgn = chess.pgn.Game.from_board(board)
        pgn.headers["Result"] = "*"
        pgn = pgn_standard_headers(pgn, player)
        # print(pgn)
        config["pgn"] = str(pgn)
        save_config()
    else:
        clean_endgame(board, lastMoveSan, humMoveSan)
else:
    clean_endgame(board, humMoveSan, None)







