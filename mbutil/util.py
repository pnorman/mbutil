import sqlite3, uuid, sys, logging, time, os, json, zlib, hashlib, tempfile

logger = logging.getLogger(__name__)


def flip_y(zoom, y):
    return (2**zoom-1) - y


def mbtiles_connect(mbtiles_file, auto_commit=False):
    try:
        con = sqlite3.connect(mbtiles_file)
        if auto_commit:
            con.isolation_level = None
        return con
    except Exception, e:
        logger.error("Could not connect to database")
        logger.exception(e)
        sys.exit(1)


def optimize_connection(cur, exclusive_lock=True):
    cur.execute("""PRAGMA journal_mode=WAL""")
    if exclusive_lock:
        cur.execute("""PRAGMA locking_mode=EXCLUSIVE""")


def compaction_prepare(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS images (
        tile_data BLOB,
        tile_id VARCHAR(256))""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS map (
        zoom_level INTEGER,
        tile_column INTEGER,
        tile_row INTEGER,
        tile_id VARCHAR(256))""")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS metadata (
        name TEXT,
        value TEXT)""")
    cur.execute("""
        CREATE UNIQUE INDEX name ON metadata (name)""")


def compaction_finalize(cur):
    try:
        cur.execute("""DROP TABLE tiles""")
    except sqlite3.OperationalError:
        pass
    cur.execute("""
        CREATE VIEW tiles AS
        SELECT map.zoom_level AS zoom_level,
        map.tile_column AS tile_column,
        map.tile_row AS tile_row,
        images.tile_data AS tile_data FROM
        map JOIN images ON images.tile_id = map.tile_id""")
    cur.execute("""
        CREATE UNIQUE INDEX map_index ON map
        (zoom_level, tile_column, tile_row)""")
    cur.execute("""
          CREATE UNIQUE INDEX images_id ON images (tile_id)""")


def mbtiles_setup(cur):
    compaction_prepare(cur)
    compaction_finalize(cur)


def optimize_database(cur, skip_analyze, skip_vacuum):
    if not skip_analyze:
        logger.info('analyzing db')
        cur.execute("""ANALYZE""")

    if not skip_vacuum:
        logger.info('cleaning db')
        cur.execute("""VACUUM""")


def optimize_database_file(mbtiles_file, skip_analyze, skip_vacuum):
    con = mbtiles_connect(mbtiles_file)
    cur = con.cursor()
    optimize_connection(cur)
    optimize_database(cur, skip_analyze, skip_vacuum)
    con.commit()
    con.close()


def mbtiles_create(mbtiles_file, **kwargs):
    logger.info("Creating empty database %s" % (mbtiles_file))
    con = mbtiles_connect(mbtiles_file)
    cur = con.cursor()
    optimize_connection(cur)
    mbtiles_setup(cur)
    con.commit()
    con.close()


def execute_commands_on_tile(command_list, image_format, tile_data):
    if command_list == None or tile_data == None:
        return tile_data

    tmp_file_fd, tmp_file_name = tempfile.mkstemp(suffix=".%s" % (image_format), prefix="tile_")
    tmp_file = os.fdopen(tmp_file_fd, "w")
    tmp_file.write(tile_data)
    tmp_file.close()

    for command in command_list:
        # logger.debug("Executing command: %s" % command)
        os.system(command % (tmp_file_name))

    tmp_file = open(tmp_file_name, "r")
    new_tile_data = tmp_file.read()
    tmp_file.close()

    os.remove(tmp_file_name)

    return new_tile_data


def execute_commands_on_file(command_list, image_format, image_file_path):
    if command_list == None or image_file_path == None or not os.path.isfile(image_file_path):
        return False

    for command in command_list:
        # logger.debug("Executing command: %s" % command)
        os.system(command % (image_file_path))

    return True


def process_tile(next_tile):
    tile_id, tile_file_path, image_format, command_list = next_tile['tile_id'], next_tile['filename'], next_tile['format'], next_tile['command_list']
    # sys.stderr.write("%s (%s) -> %s\n" % (tile_id, image_format, tile_file_path))

    tile_data = execute_commands_on_file(command_list, image_format, tile_file_path)

    return next_tile
