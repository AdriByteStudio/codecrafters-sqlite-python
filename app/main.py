import sys

from dataclasses import dataclass

# import sqlparse - available if you need it!

database_file_path = sys.argv[1]
command = sys.argv[2]


def read_varint(data, offset):
    """Parse a SQLite varint starting at offset, return (value, new_offset)."""
    value = 0
    for i in range(9):
        byte = data[offset]
        offset += 1
        if i == 8:
            value = (value << 8) | byte
            break
        value = (value << 7) | (byte & 0x7F)
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


def read_page_header(data, page_start):
    """Return (page_type, num_cells, cell_pointer_array_start, right_most_pointer) for a b-tree page.

    Page 1 has a 100-byte file header before its page header. right_most_pointer is
    only present (non-None) for interior pages.
    """
    header_offset = page_start + 100 if page_start == 0 else page_start
    page_type = data[header_offset]
    is_interior = page_type in (0x02, 0x05)
    header_size = 12 if is_interior else 8
    num_cells = int.from_bytes(data[header_offset + 3:header_offset + 5], byteorder="big")
    right_most_pointer = None
    if is_interior:
        right_most_pointer = int.from_bytes(data[header_offset + 8:header_offset + 12], byteorder="big")
    return page_type, num_cells, header_offset + header_size, right_most_pointer


def read_table_rows(data, page_start, page_size):
    """Parse a table b-tree page (leaf or interior), returning (rowid, values) per row."""
    page_type, num_cells, cell_pointer_array_start, right_most_pointer = read_page_header(data, page_start)

    rows = []
    if page_type == 0x0d:  # leaf page: cells hold the actual records
        for i in range(num_cells):
            pointer_offset = cell_pointer_array_start + i * 2
            cell_start = page_start + int.from_bytes(data[pointer_offset:pointer_offset + 2], byteorder="big")

            _, cursor = read_varint(data, cell_start)  # record size
            rowid, cursor = read_varint(data, cursor)
            rows.append((rowid, read_record_values(data, cursor)))
    else:  # interior page: cells hold child page numbers to recurse into
        for i in range(num_cells):
            pointer_offset = cell_pointer_array_start + i * 2
            cell_start = page_start + int.from_bytes(data[pointer_offset:pointer_offset + 2], byteorder="big")
            child_page_number = int.from_bytes(data[cell_start:cell_start + 4], byteorder="big")
            rows.extend(read_table_rows(data, (child_page_number - 1) * page_size, page_size))
        rows.extend(read_table_rows(data, (right_most_pointer - 1) * page_size, page_size))

    return rows


def get_table_schema(data, table_name, page_size):
    """Look up a table's (rootpage, create_sql) from sqlite_schema."""
    for _, values in read_table_rows(data, 0, page_size):
        if values[2] == table_name:
            return values[3], values[4]
    return None, None


def parse_column_names(create_sql):
    """Extract column names, in order, from a CREATE TABLE statement."""
    inner = create_sql[create_sql.index("(") + 1:create_sql.rindex(")")]
    column_names = []
    for part in inner.split(","):
        part = part.strip()
        if not part:
            continue
        column_names.append(part.split()[0].strip('"[]`'))
    return column_names


def find_integer_primary_key_index(create_sql, column_names):
    """Return the index of the INTEGER PRIMARY KEY column, or None (it aliases rowid)."""
    inner = create_sql[create_sql.index("(") + 1:create_sql.rindex(")")]
    for index, part in enumerate(inner.split(",")):
        if "integer" in part.lower() and "primary key" in part.lower():
            return index
    return None


def parse_where_clause(where_clause):
    """Parse a simple 'column = value' condition, returning (column, value)."""
    column, value = where_clause.split("=", 1)
    return column.strip(), value.strip().strip("'\"")


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
        page = database_file.read()
        page_size = int.from_bytes(page[16:18], byteorder="big")

        table_names = []
        for _, values in read_table_rows(page, 0, page_size):
            tbl_name = values[2]
            if not tbl_name.startswith("sqlite_"):  # hide internal bookkeeping tables
                table_names.append(tbl_name)

        print(" ".join(table_names))
elif command.upper().startswith("SELECT"):
    where_pos = command.upper().find(" WHERE ")
    if where_pos != -1:
        main_clause = command[:where_pos]
        where_clause = command[where_pos + len(" WHERE "):]
    else:
        main_clause = command
        where_clause = None

    parts = main_clause.split()
    from_index = next(i for i, part in enumerate(parts) if part.upper() == "FROM")
    select_clause = " ".join(parts[1:from_index])
    table_name = parts[from_index + 1]

    with open(database_file_path, "rb") as database_file:
        database_file.seek(16)
        page_size = int.from_bytes(database_file.read(2), byteorder="big")

        database_file.seek(0)
        file_contents = database_file.read()

        root_page, create_sql = get_table_schema(file_contents, table_name, page_size)
        page_start = (root_page - 1) * page_size

        if select_clause.strip().upper() == "COUNT(*)":
            rows = read_table_rows(file_contents, page_start, page_size)
            print(len(rows))
        else:
            column_names = parse_column_names(create_sql)
            int_pk_index = find_integer_primary_key_index(create_sql, column_names)
            selected_columns = [c.strip() for c in select_clause.split(",")]
            selected_indexes = [column_names.index(c) for c in selected_columns]

            rows = read_table_rows(file_contents, page_start, page_size)
            if int_pk_index is not None:  # INTEGER PRIMARY KEY column aliases the rowid
                rows = [
                    (rowid, [rowid if i == int_pk_index else v for i, v in enumerate(values)])
                    for rowid, values in rows
                ]
            if where_clause is not None:
                where_column, where_value = parse_where_clause(where_clause)
                where_column_index = column_names.index(where_column)
                rows = [(rowid, values) for rowid, values in rows if str(values[where_column_index]) == where_value]

            for _, values in rows:
                print("|".join(str(values[i]) for i in selected_indexes))
else:
    print(f"Invalid command: {command}")
