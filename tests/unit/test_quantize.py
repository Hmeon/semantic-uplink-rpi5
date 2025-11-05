from common.quantize import UniformQuantizer


def test_quantizer_placeholder():
    q = UniformQuantizer(kbits=8, xmin=-1.0, xmax=1.0)
    assert q.kbits == 8
