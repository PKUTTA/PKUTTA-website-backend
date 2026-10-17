# `tests/test_ittf_seed_positions.py`

验证淘汰赛种子签位与 ITTF 按排名设种规则一致：64 签逐批核对 1、2、3–4、5–8、9–16、17–32 号种子的签位表；8 到 128 签的种子位置上下镜像对称且每场首轮至多一名种子；同批种子在本批签位间随机抽签；不满一批时仍落在 ITTF 签位且轮空在种子同场对面。另验证按旧规则抽好的签表在后台重启后签位与比分保持不变。

运行：`python -m unittest discover -s tests -p test_ittf_seed_positions.py -v`。
