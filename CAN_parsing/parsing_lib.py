#!/usr/bin/env python
import numpy as np

class frame_and_muid(object):
    def __init__(self, frame_id, muid):
        self.frame_id = frame_id
        self.muid = muid

    def __eq__(self, other):
        return (self.frame_id == other.frame_id) and (self.muid == other.muid)

    def __lt__(self, other):
        if(((self.frame_id <= other.frame_id) and (self.muid < other.muid)) or 
           ((self.frame_id < other.frame_id))):
            return True
        else:
            return False
    def __gt__(self, other):
        if(((self.frame_id >= other.frame_id) and (self.muid > other.muid)) or 
           ((self.frame_id > other.frame_id))):
            return True
        else:
            return False

    def __str__(self):
        return f"Frame ID: {self.frame_id}; MUID: {self.muid}"

def populate_dict(dict_name, filename):
    dict_name['filename'] = filename
    dict_name['messages'] = np.loadtxt(filename,delimiter=',',dtype=int,skiprows=1, usecols=(6,7,8,9,10,11,12,13),converters=lambda x: int(x,16))
    dict_name['timestamps'] = np.loadtxt(filename,delimiter=',',dtype=int,skiprows=1, usecols=(0))
    dict_name['ids'] = np.loadtxt(filename,delimiter=',',skiprows=1, dtype=int,usecols=(1),converters=lambda x : int(x,16))
    dict_name['hex_ids'] = np.loadtxt(filename,delimiter=',',dtype=str,skiprows=1, usecols=(1))
    dict_name['hex_messages'] = np.loadtxt(filename,delimiter=',',dtype=str,skiprows=1, usecols=(6,7,8,9,10,11,12,13))

def populate_dict_panda(dict_name, filename):
    dict_name['filename'] = filename
    dict_name['hex_message_block'] = np.loadtxt(filename,delimiter=',',dtype='S16',skiprows=1, usecols=(2), converters=lambda x : x[2:])
    dict_name['hex_messages'] = dict_name['hex_message_block'].view('S2').reshape(dict_name['hex_message_block'].shape[0],8)
    dict_name['hex_ids'] = np.loadtxt(filename,delimiter=',',dtype=str,skiprows=1, usecols=(1))
    v_int = np.vectorize(lambda x: int(x, 16),otypes=[np.uint8])
    dict_name['messages'] = v_int(dict_name['hex_messages'])
    dict_name['ids'] = np.loadtxt(filename,delimiter=',',skiprows=1, dtype=np.uint16,usecols=(1),converters=lambda x : int(x,16))
    dict_name['timestamps'] = np.loadtxt(filename,delimiter=',',dtype=float,skiprows=1, usecols=(4))
    dict_name['bus'] = np.loadtxt(filename,delimiter=',',dtype=np.uint8,skiprows=1, usecols=(0))

def clean_bad_timestamps(dict_name):
    diff = np.diff(dict_name['timestamps'])
    mean_diff = diff.mean()
    any_jump = np.nonzero(np.abs((diff-mean_diff)/mean_diff) > 2)[0]
    drop = np.nonzero(diff < 0)[0]
    print(f"Identified {len(drop)} drops in timestamp and {len(any_jump)} total timestamp jumps.")
    if(len(drop) == 1):
        split_index = drop[0]+1
        timestamp_length = len(dict_name['timestamps'])
        for key in dict_name.keys():
            if(isinstance(dict_name.get(key),np.ndarray) and dict_name.get(key).shape[0] == timestamp_length):
                dict_name[key] = dict_name[key][split_index:]

def calculate_unique_message_id(dict_name):
    assert dict_name['messages'].shape[1] == 8
    dict_name['messages_unique_ids'] = np.zeros((dict_name['messages'].shape[0],),dtype=np.uint64)
    for i in range(8):
        dict_name['messages_unique_ids'] += dict_name['messages'][:,i].astype(np.uint64)*16**(16-2*(i+1))

def return_all_messages_for_frame(dict_name, frame_id,return_hex=False):
    if(return_hex):
        return dict_name['hex_messages'][np.argwhere(dict_name['ids'] == frame_id)[:,0]]
    else:
        return dict_name['messages'][np.argwhere(dict_name['ids'] == frame_id)[:,0]]

def return_all_frames_messages_for_muid_and_frame(dict_name, frame_id, muid,return_hex=False):
    indices = np.argwhere((dict_name['messages_unique_ids'] == muid)*(dict_name['ids'] == frame_id))[:,0]
    for index in indices:
        if(return_hex):
            return frame_and_muid(dict_name['hex_ids'][index],
                    dict_name['hex_messages'][index])
        else:
            return frame_and_muid(dict_name['ids'][index],
                    dict_name['messages'][index])

def return_unique_messages_for_frame(dict_name, frame_id, return_hex=False):
    messages = []
    unique_muids = np.unique(dict_name['messages_unique_ids'][np.argwhere(dict_name['ids'] == frame_id)[:,0]])
    for unique_muid in unique_muids:
        first_index = np.argwhere((dict_name['messages_unique_ids'] == unique_muid)*(dict_name['ids'] == frame_id))[0,0]
        if(return_hex):
            messages.append(dict_name['hex_messages'][first_index])
        else:
            messages.append(dict_name['messages'][first_index])
    return messages

def return_frame_IDs_with_message_changes(dict_name):
    frame_ids = []
    for frame_id in np.unique(dict_name['ids']):
        unique_muids = np.unique(dict_name['messages_unique_ids'][np.argwhere(dict_name['ids'] == frame_id)[:,0]])
        if(len(unique_muids) > 1):
            frame_ids.append([frame_id,len(unique_muids)])
    return frame_ids

def return_frame_IDs_muids(dict_name, reject_muids = [], bus = None):
    '''
    Return a list of all frame IDs and muids for a log.
    '''
    frames_and_muids = []
    for frame_id in np.unique(dict_name['ids']):
        if(bus):
            unique_muids = np.unique(dict_name['messages_unique_ids'][np.argwhere((dict_name['ids'] == frame_id)*(dict_name['bus'] == bus))[:,0]])
        else:
            unique_muids = np.unique(dict_name['messages_unique_ids'][np.argwhere(dict_name['ids'] == frame_id)[:,0]])
        for unique_muid in unique_muids:
            if(unique_muid not in reject_muids):
                indices = np.argwhere((dict_name['messages_unique_ids'] == unique_muid)*(dict_name['ids'] == frame_id))[:,0]
                frames_and_muids.append(frame_and_muid(frame_id,unique_muid))
    return frames_and_muids

def return_frame_IDs_muids_with_message_changes_in_timestamp_range(dict_name,timestamp_range, reject_muids = [], threshold = 10, only_within_range=True):
    '''
    This is a search for control signals, rather than physical sensors. Physical sensors can have a nearly infinite number of values.
    '''
    frames_and_muids = []
    for frame_id in np.unique(dict_name['ids']):
        unique_muids = np.unique(dict_name['messages_unique_ids'][np.argwhere(dict_name['ids'] == frame_id)[:,0]])
        if(len(unique_muids) < threshold):
            for unique_muid in unique_muids:
                if(unique_muid not in reject_muids):
                    indices = np.argwhere((dict_name['messages_unique_ids'] == unique_muid)*(dict_name['ids'] == frame_id))[:,0]
                    other_muid_indices = np.argwhere((dict_name['messages_unique_ids'] != unique_muid)*(dict_name['ids'] == frame_id))[:,0]
                    timestamps = dict_name['timestamps'][indices]
                    if(only_within_range and timestamps[0] > timestamp_range[0] and timestamps[-1] < timestamp_range[-1]):
                        frames_and_muids.append(frame_and_muid(frame_id,unique_muid))
                    elif(only_within_range == False and ((timestamps > timestamp_range[0])*(timestamps < timestamp_range[1])).sum() and len(other_muid_indices)):
                        frames_and_muids.append(frame_and_muid(frame_id,unique_muid))
    return frames_and_muids

def iterative_intersect(list_of_sets):
    assert len(list_of_sets) > 1
    intersection = list_of_sets[0]
    for i in range(1,len(list_of_sets)):
        intersection = np.intersect1d(intersection,list_of_sets[i])
    return intersection
def return_interesting_timestamps(dict_name,frame_ids):
    timestamps = []
    for frame_id in frame_ids:
        indices = np.argwhere(dict_name['ids'] == frame_id)[:,0]
        if(len(indices)):
            max_diff = np.argmax(np.abs(np.diff(dict_name['messages_unique_ids'][indices])))
            timestamps.append(dict_name['timestamps'][indices][max_diff])
    return timestamps

def return_frame_series(dict_name, frame_id, bus=None):
    '''
    Return (timestamps, messages) for a single frame ID, in chronological order.
    If `bus` is given, only messages on that bus are included.
    '''
    if(bus is not None):
        indices = np.argwhere((dict_name['ids'] == frame_id)*(dict_name['bus'] == bus))[:,0]
    else:
        indices = np.argwhere(dict_name['ids'] == frame_id)[:,0]
    if(len(indices) == 0):
        return np.array([]), np.empty((0,8), dtype=dict_name['messages'].dtype)
    order = np.argsort(dict_name['timestamps'][indices], kind='stable')
    indices = indices[order]
    return dict_name['timestamps'][indices], dict_name['messages'][indices]

def return_value_transitions(timestamps, values):
    '''
    Given parallel timestamp and value arrays, return the indices, times,
    from-values and to-values where the value changes between consecutive samples.
    '''
    if(len(values) < 2):
        return np.array([], dtype=int), np.array([], dtype=timestamps.dtype), np.array([]), np.array([])
    change_mask = np.zeros(len(values), dtype=bool)
    change_mask[1:] = values[1:] != values[:-1]
    indices = np.argwhere(change_mask)[:,0]
    from_values = values[indices-1]
    to_values = values[indices]
    return indices, timestamps[indices], from_values, to_values

def return_frame_IDs_with_limited_message_changes(dict_name, threshold=10, bus=None):
    '''
    Search for control-signal candidate frames: frames whose message payload
    takes a small (1 < n_unique <= threshold) set of distinct values, i.e. they
    change between a limited set of discrete states. Physical sensors usually
    have a near-infinite number of values and are excluded by `threshold`.
    '''
    frames = []
    for frame_id in np.unique(dict_name['ids']):
        if(bus is not None):
            indices = np.argwhere((dict_name['ids'] == frame_id)*(dict_name['bus'] == bus))[:,0]
        else:
            indices = np.argwhere(dict_name['ids'] == frame_id)[:,0]
        if(len(indices) == 0):
            continue
        unique_count = len(np.unique(dict_name['messages_unique_ids'][indices]))
        if(1 < unique_count <= threshold):
            frames.append((frame_id, unique_count))
    return frames

