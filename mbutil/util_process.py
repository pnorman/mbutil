import sqlite3, uuid, sys, logging, time, os, json, zlib, hashlib, tempfile, multiprocessing

from util import mbtiles_connect, mbtiles_setup, optimize_connection, optimize_database, process_tile
from multiprocessing import Pool

logger = logging.getLogger(__name__)


def execute_commands_on_mbtiles(mbtiles_file, **kwargs):
    logger.info("Executing commands on database %s" % (mbtiles_file))


    if kwargs.get('command_list') == None or len(kwargs['command_list']) == 0:
        return

    auto_commit = kwargs.get('auto_commit', False)
    zoom        = kwargs.get('zoom', -1)
    min_zoom    = kwargs.get('min_zoom', 0)
    max_zoom    = kwargs.get('max_zoom', 255)
    default_pool_size = kwargs.get('poolsize', -1)

    if zoom >= 0:
        min_zoom = max_zoom = zoom


    con = mbtiles_connect(mbtiles_file, auto_commit)
    cur = con.cursor()
    optimize_connection(cur)


    existing_mbtiles_is_compacted = (con.execute("select count(name) from sqlite_master where type='table' AND name='images';").fetchone()[0] > 0)
    if not existing_mbtiles_is_compacted:
        logger.info("The mbtiles file must be compacted, exiting...")
        return

    image_format = 'png'
    try:
        image_format = con.execute("select value from metadata where name='format';").fetchone()[0]
    except:
        pass


    count = 0
    duplicates = 0
    chunk = 1000
    start_time = time.time()
    processed_tile_ids = set()

    max_rowid = (con.execute("select max(rowid) from map").fetchone()[0])
    total_tiles = (con.execute("""select count(distinct(tile_id)) from map where zoom_level>=? and zoom_level<=?""",
        (min_zoom, max_zoom)).fetchone()[0])

    logger.debug("%d tiles to process" % (total_tiles))


    logger.debug("Creating an index for the tile_id column...")
    con.execute("""CREATE INDEX IF NOT EXISTS tile_id_index ON map (tile_id)""")
    logger.debug("...done")


    if default_pool_size < 1:
        default_pool_size = None
        logger.debug("Using default pool size")
    else:
        logger.debug("Using pool size = %d" % (default_pool_size))

    pool = Pool(default_pool_size)
    multiprocessing.log_to_stderr(logger.level)


    for i in range((max_rowid / chunk) + 1):
        # logger.debug("Starting range %d-%d" % (i*chunk, (i+1)*chunk))
        tiles = cur.execute("""select images.tile_id, images.tile_data, map.zoom_level, map.tile_column, map.tile_row
            from map, images
            where (map.rowid > ? and map.rowid <= ?)
            and (map.zoom_level>=? and map.zoom_level<=?)
            and (images.tile_id == map.tile_id)""",
            ((i * chunk), ((i + 1) * chunk), min_zoom, max_zoom))


        tiles_to_process = []

        t = tiles.fetchone()

        while t:
            tile_id = t[0]
            tile_data = t[1]
            # tile_z = t[2]
            # tile_x = t[3]
            # tile_y = t[4]
            # logging.debug("Working on tile (%d, %d, %d)" % (tile_z, tile_x, tile_y))

            if tile_id in processed_tile_ids:
                duplicates = duplicates + 1
            else:
                processed_tile_ids.add(tile_id)

                tmp_file_fd, tmp_file_name = tempfile.mkstemp(suffix=".%s" % (image_format), prefix="tile_")
                tmp_file = os.fdopen(tmp_file_fd, "w")
                tmp_file.write(tile_data)
                tmp_file.close()

                tiles_to_process.append({
                    'tile_id' : tile_id,
                    'filename' : tmp_file_name,
                    'format' : image_format,
                    'command_list' : kwargs.get('command_list', [])
                })

            t = tiles.fetchone()


        if len(tiles_to_process) == 0:
            continue

        # Execute commands in parallel
        # logger.debug("Starting multiprocessing...")
        processed_tiles = pool.map(process_tile, tiles_to_process)

	# logger.debug("Starting reimport...")
        for next_tile in processed_tiles:
            tile_id, tile_file_path = next_tile['tile_id'], next_tile['filename']

            tmp_file = open(tile_file_path, "r")
            tile_data = tmp_file.read()
            tmp_file.close()

            os.remove(tile_file_path)

            if tile_data and len(tile_data) > 0:
                m = hashlib.md5()
                m.update(tile_data)
                new_tile_id = m.hexdigest()

                cur.execute("""insert or ignore into images (tile_id, tile_data) values (?, ?)""",
                    (new_tile_id, sqlite3.Binary(tile_data)))
                cur.execute("""update map set tile_id=? where tile_id=?""",
                    (new_tile_id, tile_id))
                if tile_id != new_tile_id:
                    cur.execute("""delete from images where tile_id=?""",
                    [tile_id])

                # logger.debug("Tile %s done\n" % (tile_id, ))


            count = count + 1
            if (count % 100) == 0:
                logger.debug("%s tiles finished (%.1f%%, %.1f tiles/sec)" %
                    (count, (float(count) / float(total_tiles)) * 100.0, count / (time.time() - start_time)))


    logger.info("%s tiles finished, %d duplicates ignored (100.0%%, %.1f tiles/sec)" %
        (count, duplicates, count / (time.time() - start_time)))

    pool.close()
    con.commit()
    con.close()
