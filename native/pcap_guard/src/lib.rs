use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

const DEFAULT_MAX_FILE: usize = 64 * 1024 * 1024;
const DEFAULT_MAX_PACKET: usize = 262_144;
const DEFAULT_MAX_PACKETS: usize = 100_000;

#[derive(Debug, PartialEq)]
struct Header {
    major: u16,
    minor: u16,
    snaplen: u32,
    link_type: u32,
    little_endian: bool,
    resolution: u32,
}

fn invalid(message: &str) -> PyErr {
    PyValueError::new_err(message.to_owned())
}

fn read_u16(data: &[u8], offset: usize, little: bool) -> u16 {
    let bytes = [data[offset], data[offset + 1]];
    if little { u16::from_le_bytes(bytes) } else { u16::from_be_bytes(bytes) }
}

fn read_u32(data: &[u8], offset: usize, little: bool) -> u32 {
    let bytes = [data[offset], data[offset + 1], data[offset + 2], data[offset + 3]];
    if little { u32::from_le_bytes(bytes) } else { u32::from_be_bytes(bytes) }
}

fn parse_header(payload: &[u8], max_packet: usize) -> Result<Header, &'static str> {
    if payload.len() < 24 {
        return Err("PCAP global header is truncated");
    }
    let (little_endian, resolution) = match &payload[..4] {
        [0xd4, 0xc3, 0xb2, 0xa1] => (true, 1_000_000),
        [0xa1, 0xb2, 0xc3, 0xd4] => (false, 1_000_000),
        [0x4d, 0x3c, 0xb2, 0xa1] => (true, 1_000_000_000),
        [0xa1, 0xb2, 0x3c, 0x4d] => (false, 1_000_000_000),
        _ => return Err("unsupported PCAP magic; classic PCAP is required"),
    };
    let major = read_u16(payload, 4, little_endian);
    let minor = read_u16(payload, 6, little_endian);
    if (major, minor) != (2, 4) {
        return Err("unsupported classic PCAP version");
    }
    let snaplen = read_u32(payload, 16, little_endian);
    let link_type = read_u32(payload, 20, little_endian);
    if snaplen == 0 || snaplen as usize > max_packet {
        return Err("PCAP snaplen is outside the supported packet-size limit");
    }
    if link_type > 0xffff {
        return Err("PCAP link type contains unsupported flag bits");
    }
    Ok(Header { major, minor, snaplen, link_type, little_endian, resolution })
}

fn parse_records(payload: &[u8], header: &Header, max_packet: usize, max_packets: usize)
    -> Result<Vec<(u32, u32, u32, u32, Vec<u8>)>, &'static str>
{
    let mut records = Vec::new();
    let mut offset = 24usize;
    while offset < payload.len() {
        if records.len() >= max_packets {
            return Err("PCAP packet count exceeds the configured limit");
        }
        if payload.len() - offset < 16 {
            return Err("PCAP packet record header is truncated");
        }
        let seconds = read_u32(payload, offset, header.little_endian);
        let fraction = read_u32(payload, offset + 4, header.little_endian);
        let captured = read_u32(payload, offset + 8, header.little_endian);
        let original = read_u32(payload, offset + 12, header.little_endian);
        offset += 16;
        if fraction >= header.resolution {
            return Err("PCAP packet timestamp fraction is out of range");
        }
        let captured_usize = captured as usize;
        if captured_usize > header.snaplen as usize || captured_usize > max_packet {
            return Err("PCAP captured length exceeds the configured limit");
        }
        if original < captured {
            return Err("PCAP original length is smaller than captured length");
        }
        if captured_usize > payload.len() - offset {
            return Err("PCAP packet data is truncated");
        }
        let end = offset.checked_add(captured_usize)
            .ok_or("PCAP packet length arithmetic overflow")?;
        records.push((seconds, fraction, captured, original, payload[offset..end].to_vec()));
        offset = end;
    }
    Ok(records)
}

#[pyfunction]
fn parse_pcap(
    payload: &Bound<'_, PyBytes>,
    max_file_bytes: usize,
    max_packet_bytes: usize,
    max_packets: usize,
) -> PyResult<(u16, u16, u32, u32, String, u32, Vec<(u32, u32, u32, u32, Vec<u8>)>)> {
    let data = payload.as_bytes();
    if max_file_bytes == 0 || max_file_bytes > DEFAULT_MAX_FILE
        || max_packet_bytes == 0 || max_packet_bytes > DEFAULT_MAX_PACKET
        || max_packets == 0 || max_packets > DEFAULT_MAX_PACKETS {
        return Err(invalid("configured PCAP limits are outside supported bounds"));
    }
    if data.len() > max_file_bytes {
        return Err(invalid("PCAP input exceeds the configured file-size limit"));
    }
    let header = parse_header(data, max_packet_bytes).map_err(invalid)?;
    let packets = parse_records(data, &header, max_packet_bytes, max_packets).map_err(invalid)?;
    Ok((
        header.major,
        header.minor,
        header.snaplen,
        header.link_type,
        if header.little_endian { "little" } else { "big" }.to_owned(),
        header.resolution,
        packets,
    ))
}

#[pymodule]
fn _yuki_pcap_guard(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse_pcap, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(little: bool) -> Vec<u8> {
        let mut bytes = vec![0u8; 24];
        bytes[..4].copy_from_slice(if little { &[0xd4,0xc3,0xb2,0xa1] } else { &[0xa1,0xb2,0xc3,0xd4] });
        let put16 = |v: u16| if little { v.to_le_bytes() } else { v.to_be_bytes() };
        let put32 = |v: u32| if little { v.to_le_bytes() } else { v.to_be_bytes() };
        bytes[4..6].copy_from_slice(&put16(2));
        bytes[6..8].copy_from_slice(&put16(4));
        bytes[16..20].copy_from_slice(&put32(65535));
        bytes[20..24].copy_from_slice(&put32(1));
        bytes
    }

    #[test]
    fn accepts_both_byte_orders() {
        for little in [true, false] {
            let bytes = fixture(little);
            let header = parse_header(&bytes, DEFAULT_MAX_PACKET).unwrap();
            assert_eq!(header.little_endian, little);
            assert_eq!(header.link_type, 1);
        }
    }

    #[test]
    fn rejects_truncated_record_header() {
        let mut bytes = fixture(true);
        bytes.extend_from_slice(&[0; 8]);
        let header = parse_header(&bytes, DEFAULT_MAX_PACKET).unwrap();
        assert_eq!(parse_records(&bytes, &header, DEFAULT_MAX_PACKET, DEFAULT_MAX_PACKETS),
                   Err("PCAP packet record header is truncated"));
    }

    #[test]
    fn rejects_invalid_timestamp_fraction() {
        let mut bytes = fixture(true);
        bytes.extend_from_slice(&[0; 16]);
        bytes[28..32].copy_from_slice(&1_000_000u32.to_le_bytes());
        let header = parse_header(&bytes, DEFAULT_MAX_PACKET).unwrap();
        assert_eq!(parse_records(&bytes, &header, DEFAULT_MAX_PACKET, DEFAULT_MAX_PACKETS),
                   Err("PCAP packet timestamp fraction is out of range"));
    }

    #[test]
    fn rejects_oversized_snaplen() {
        let mut bytes = fixture(true);
        bytes[16..20].copy_from_slice(&(DEFAULT_MAX_PACKET as u32 + 1).to_le_bytes());
        assert_eq!(parse_header(&bytes, DEFAULT_MAX_PACKET),
                   Err("PCAP snaplen is outside the supported packet-size limit"));
    }
}
