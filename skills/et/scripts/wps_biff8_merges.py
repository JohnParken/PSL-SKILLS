"""BIFF8 merge intersection checks without expanding ranges into cells."""


class MergeOverlapError(ValueError):
    def __init__(self, index, row):
        self.merge_index = index
        super().__init__('Overlapping merges unsupported at merge index '
                         + str(index) + ', row ' + str(row))


def validate_merge_ranges(ranges):
    """Sweep inclusive row intervals using a 256-bit occupied-column mask.

    Work is O(n log n) with two events per range, independent of rectangle area.
    Adjacent rows/columns are permitted; sharing even one cell is rejected.
    """
    events = []
    for index, bounds in enumerate(ranges):
        if (len(bounds) != 4 or any(type(value) is not int for value in bounds)):
            raise ValueError('Invalid merge bounds')
        first_row, last_row, first_col, last_col = bounds
        if not (0 <= first_row <= last_row < 65536
                and 0 <= first_col <= last_col < 256
                and (first_row, first_col) != (last_row, last_col)):
            raise ValueError('Invalid merge bounds')
        mask = ((1 << (last_col - first_col + 1)) - 1) << first_col
        events.append((first_row, 1, index, mask))
        events.append((last_row + 1, 0, index, mask))
    events.sort()
    occupied = 0
    for row, action, index, mask in events:
        if action:
            if occupied & mask:
                raise MergeOverlapError(index, row)
            occupied |= mask
        else:
            occupied ^= mask
