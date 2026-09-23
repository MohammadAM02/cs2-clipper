from clipper.windows import downloads_dir, keep_awake


def test_downloads_dir_is_an_existing_absolute_folder():
    path = downloads_dir()
    assert path.is_absolute()
    assert path.is_dir()


def test_keep_awake_enters_and_leaves_cleanly():
    with keep_awake():
        pass
