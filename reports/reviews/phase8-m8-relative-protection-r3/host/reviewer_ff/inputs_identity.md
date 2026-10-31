# 边界探针输入身份(本轮不复制板级原字节, 只登记仓库内的同字节来源)

| 探针输入 | 大小 | SHA-256 | 仓库内同字节来源 |
| --- | --- | --- | --- |
| `prior_raw/p0_boot.bin` | 3381 | `bc47dc022b25bd483c16ab749895bf2a9d7dba318fdf88826357d2dbb98214fa` | `reports/reviews/phase8-motor-m7-p0-board/S3_raw_boot.bin.b64`(base64 解码后逐字节相同) |
| `prior_raw/cp3_wire_archive.md` | 7925 | `5f84d459386c52bebc6c13b82debfaced06db74d241a54cd1f264618bf610819` | `reports/reviews/phase8-target-measurement-a0-board-b/cp3_wire_archive.md`(逐字节相同, 未复制) |

重放 `TB01..TB04` 需要的那份 3381 B 启动件按下面这条命令从仓库内既有 base64 件解出, 解出的字节 SHA-256 已在 `ff_identity.json` 里登记:

```text
python -c "import base64,pathlib;pathlib.Path('prior_raw/p0_boot.bin').write_bytes(base64.b64decode(''.join(pathlib.Path('D:/ns800/reports/reviews/phase8-motor-m7-p0-board/S3_raw_boot.bin.b64').read_text().split())))"
```

`002_boot.rx.bin`(3757 B, `97b37f32aa03c86b8708ae088d0ae65a8f92520f1a1ed70beb74663049321749`)不需要另外落文件: 探针自己从 `cp3_wire_archive.md` 的 base64 段解码。
