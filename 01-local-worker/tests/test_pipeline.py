from local_worker.pipeline.run import build_parser


def test_parser_accepts_all_stages() -> None:
    parser = build_parser()
    args = parser.parse_args(["all"])
    assert args.stage == "all"
