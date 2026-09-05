import sys

from dataclasses import dataclass

# import sqlparse - available if you need it!

database_file_path = sys.argv[1]
command = sys.argv[2]


def read_varint(data, offset):
    """Parse a SQLite varint starting at offset, return (value, new_offset)."""
    value = 0
    for i in range(9):
        byte = data[offset + i]
        if i == 8:
            value = (value << 8) | byte
            offset += 1
            break
        value = (value << 7) | (byte & 0x7F)
        offset += 1
        if not (byte & 0x80):
            break
    return value, offset


def read_record_values(data, record_start):
    """Parse a record's header + body, returning the list of column values."""
    header_size, cursor = read_varint(data, record_start)
    header_end = record_start + header_size
    serial_types = []
    while cursor < header_end:
        serial_type, cursor = read_varint(data, cursor)
        serial_types.append(serial_type)

    values = []
    body_cursor = header_end
    for serial_type in serial_types:
        if serial_type == 0:
            values.append(None)
        elif serial_type == 1:
            values.append(int.from_bytes(data[body_cursor:body_cursor + 1], "big", signed=True))
            body_cursor += 1
        elif serial_type == 2:
            values.append(int.from_bytes(data[body_cursor:body_cursor + 2], "big", signed=True))
            body_cursor += 2
        elif serial_type == 3:
            values.append(int.from_bytes(data[body_cursor:body_cursor + 3], "big", signed=True))
            body_cursor += 3
        elif serial_type == 4:
            values.append(int.from_bytes(data[body_cursor:body_cursor + 4], "big", signed=True))
            body_cursor += 4
        elif serial_type == 5:
            values.append(int.from_bytes(data[body_cursor:body_cursor + 6], "big", signed=True))
            body_cursor += 6
        elif serial_type == 6:
            values.append(int.from_bytes(data[body_cursor:body_cursor + 8], "big", signed=True))
            body_cursor += 8
        elif serial_type == 7:
            import struct
            values.append(struct.unpack(">d", data[body_cursor:body_cursor + 8])[0])
            body_cursor += 8
        elif serial_type == 8:
            values.append(0)
        elif serial_type == 9:
            values.append(1)
        elif serial_type >= 12 and serial_type % 2 == 0:
            length = (serial_type - 12) // 2
            values.append(data[body_cursor:body_cursor + length])
            body_cursor += length
        else:
            length = (serial_type - 13) // 2
            values.append(data[body_cursor:body_cursor + length].decode())
            body_cursor += length

    return values


if command == ".dbinfo":
    with open(database_file_path, "rb") as database_file:
        # You can use print statements as follows for debugging, they'll be visible when running tests.
        print("Logs from your program will appear here!", file=sys.stderr)

        database_file.seek(16)  # Skip the first 16 bytes of the header
        page_size = int.from_bytes(database_file.read(2), byteorder="big")

        # The sqlite_schema page header starts right after the 100-byte file header.
        # Cell count is a 2-byte big-endian value at offset 3 of the page header.
        database_file.seek(103)
        number_of_tables = int.from_bytes(database_file.read(2), byteorder="big")

        print(f"database page size: {page_size}")
        print(f"number of tables: {number_of_tables}")
elif command == ".tables":
    with open(database_file_path, "rb") as database_file:
        database_file.seek(0)
        page = database_file.read()

        number_of_cells = int.from_bytes(page[103:105], byteorder="big")

        # Cell pointer array starts right after the 8-byte leaf page header (which
        # follows the 100-byte file header).
        cell_pointer_array_start = 108
        table_names = []
        for i in range(number_of_cells):
            pointer_offset = cell_pointer_array_start + i * 2
            cell_start = int.from_bytes(page[pointer_offset:pointer_offset + 2], byteorder="big")

            _, cursor = read_varint(page, cell_start)  # record size
            _, cursor = read_varint(page, cursor)  # rowid
            values = read_record_values(page, cursor)
            tbl_name = values[2]
            if not tbl_name.startswith("sqlite_"):  # hide internal bookkeeping tables
                table_names.append(tbl_name)

        print(" ".join(table_names))
else:
    print(f"Invalid command: {command}")
